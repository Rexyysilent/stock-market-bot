"""Source-time bounds and usable-feed fallback, using synthetic observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from agents.twitter_agent import TwitterAgent
from timeutil import split_fresh_records


NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def post(identity, date, *, account="@first"):
    return {"source": "X/Twitter", "account": account,
            "title": f"Synthetic post {identity}", "link": f"https://fixture.example/{identity}",
            "date": date, "query": account.lstrip("@")}


class FreshnessTests(unittest.TestCase):
    def test_window_is_closed_at_both_ends(self):
        rows = [{"date": "2026-11-18T12:00:00Z", "id": "cutoff"},
                {"date": "2026-11-25T12:00:00Z", "id": "now"},
                {"date": "2026-11-25T17:30:01+05:30", "id": "future"},
                {"date": "2026-11-18T11:59:59Z", "id": "stale"}]
        fresh, dropped = split_fresh_records(rows, "date", 7, now=NOW)
        self.assertEqual([r["id"] for r in fresh], ["cutoff", "now"])
        self.assertEqual([(r["id"], r["drop_reason"]) for r in dropped],
                         [("future", "future"), ("stale", "stale")])

    def test_future_rss_timestamp_is_retained_only_in_exclusions(self):
        rows = [post("future", "Thu, 01 Jan 2099 12:00:00 GMT"), post("missing", "")]
        original = deepcopy(rows)
        fresh, dropped = split_fresh_records(rows, "date", 7, now=NOW)
        self.assertEqual(fresh, [])
        self.assertEqual([r["drop_reason"] for r in dropped], ["future", "undated"])
        self.assertEqual(dropped[0]["as_of"], "2099-01-01T12:00:00Z")
        self.assertEqual(rows, original)

    def test_aware_clock_offsets_give_the_same_decision(self):
        rows = [post("fresh", "2026-11-25T11:59:59Z"),
                post("future", "2026-11-25T12:00:01Z")]
        offset_clock = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(split_fresh_records(rows, "date", 7, now=NOW),
                         split_fresh_records(rows, "date", 7, now=offset_clock))

    def test_naive_run_clock_is_utc_without_losing_its_time_of_day(self):
        rows = [post("fresh", "2026-11-25T11:59:59Z"),
                post("future", "2026-11-25T12:00:01Z")]
        self.assertEqual(split_fresh_records(rows, "date", 7, now=NOW),
                         split_fresh_records(rows, "date", 7, now=NOW.replace(tzinfo=None)))


class SyntheticTwitter(TwitterAgent):
    def __init__(self, primary=(), fallback=(), *, accounts=("first",)):
        super().__init__(now=NOW)
        self.primary = list(primary)
        self.fallback = list(fallback)
        self.accounts = accounts
        self.fallback_calls = 0

    def get_twitter_via_nitter(self, max_accounts=5):
        self.health.update(source="Nitter RSS", nitter_available=True,
                           nitter_accounts_expected=len(self.accounts),
                           nitter_accounts_http_ok=len(self.accounts),
                           nitter_accounts_with_items=len(self.accounts))
        return deepcopy(self.primary)

    def get_twitter_via_rss(self, max_queries=3):
        self.fallback_calls += 1
        self.health.update(fallback_used=True, source="Google News RSS")
        return deepcopy(self.fallback)


class TwitterFreshnessTests(unittest.TestCase):
    def test_stale_only_primary_uses_fresh_fallback(self):
        agent = SyntheticTwitter([post("stale", "2026-10-01T12:00:00Z")],
                                 [post("fresh", "2026-11-25T11:00:00Z", account="GoogleNews")])
        rows = agent.get_twitter_intel()
        self.assertEqual([r["title"] for r in rows], ["Synthetic post fresh"])
        self.assertEqual(agent.fallback_calls, 1)
        self.assertEqual(agent.health["status"], "WARN")
        self.assertEqual(agent.health["freshness_dropped"], {"stale": 1})
        self.assertEqual(agent.get_dropped_twitter_signals()[0]["drop_reason"], "stale")

    def test_future_primary_and_fallback_do_not_become_signals(self):
        agent = SyntheticTwitter([post("future", "2099-01-01T12:00:00Z")],
                                 [post("fallback-future", "2099-01-02T12:00:00Z")])
        self.assertEqual(agent.get_twitter_intel(), [])
        self.assertEqual(agent.fallback_calls, 1)
        self.assertEqual(agent.health["signals"], 0)
        self.assertEqual(agent.health["status"], "WARN")
        self.assertEqual(agent.health["freshness_dropped"], {"future": 2})
        self.assertEqual(len(agent.get_dropped_twitter_signals()), 2)

    def test_invalid_duplicate_cannot_mask_a_fresh_occurrence(self):
        agent = SyntheticTwitter([post("same", "2099-01-01T12:00:00Z"),
                                  post("same", "2026-11-25T11:00:00Z")])
        rows = agent.get_twitter_intel()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-25T11:00:00Z")
        self.assertEqual(agent.fallback_calls, 0)
        self.assertEqual(agent.health["freshness_dropped"], {"future": 1})
        self.assertEqual(agent.health["status"], "WARN")

    def test_raw_account_items_do_not_establish_fresh_account_coverage(self):
        agent = SyntheticTwitter([
            post("fresh", "2026-11-25T11:00:00Z"),
            post("stale", "2026-10-01T12:00:00Z", account="@second"),
        ], accounts=("first", "second"))
        self.assertEqual(len(agent.get_twitter_intel()), 1)
        self.assertEqual(agent.health["nitter_accounts_with_items"], 2)
        self.assertEqual(agent.health["nitter_accounts_with_fresh_items"], 1)
        self.assertEqual(agent.health["status"], "WARN")

    def test_healthy_primary_avoids_extra_requests_and_deduplicates(self):
        row = post("fresh", "2026-11-25T11:00:00Z")
        agent = SyntheticTwitter([row, row])
        self.assertEqual(agent.get_twitter_intel(), [row])
        self.assertEqual(agent.fallback_calls, 0)
        self.assertEqual(agent.health["status"], "OK")
        self.assertEqual(agent.health["freshness_dropped"], {})

    def test_explicit_fallback_uses_the_same_bounds(self):
        agent = SyntheticTwitter(fallback=[post("fresh", "2026-11-25T11:00:00Z"),
                                            post("future", "2099-01-01T12:00:00Z")])
        rows = agent.get_twitter_intel(prefer_nitter=False)
        self.assertEqual([r["title"] for r in rows], ["Synthetic post fresh"])
        self.assertEqual(agent.health["freshness_dropped"], {"future": 1})

    def test_new_run_resets_drop_audit(self):
        agent = SyntheticTwitter([post("future", "2099-01-01T12:00:00Z")])
        agent.get_twitter_intel()
        agent.primary = [post("fresh", "2026-11-25T11:00:00Z")]
        agent.get_twitter_intel()
        self.assertEqual(agent.get_dropped_twitter_signals(), [])
        self.assertEqual(agent.health["freshness_dropped"], {})

    def test_top_level_health_preserves_fresh_coverage_and_future_warnings(self):
        from export_for_gemini import build_export_health

        agent = SyntheticTwitter([
            post("fresh", "2026-11-25T11:00:00Z"),
            post("future", "2099-01-01T12:00:00Z", account="@second"),
        ], accounts=("first", "second"))
        rows = agent.get_twitter_intel()
        health = build_export_health(
            generated_at="2026-11-25T12:00:00Z", elapsed=1, whispers=[], prices={},
            technicals={}, twitter_data=rows, sec_filings=[], options_flow={},
            earnings_cal=[], ceo_signals=[], all_option_anomalies=[], news_agent=None,
            social_agent=None, openinsider_agent=None, twitter_agent=agent, sec_agent=None,
            headline_pool={"fetched_count": 5, "fresh_before_relevance": 5, "selected_count": 5},
        )
        self.assertTrue(any("covered 1/2" in warning for warning in health["warnings"]))
        self.assertTrue(any("excluded 1 future or undated" in warning for warning in health["warnings"]))
        self.assertEqual(health["sources"]["twitter"]["freshness_dropped"], {"future": 1})


class ResearchFreshnessTests(unittest.TestCase):
    def test_future_qualified_ceo_row_is_excluded_before_rendering_and_mining(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent(now=NOW)
        agent.uranium_tickers = ["NXE"]

        def row(identity, text, stamp):
            return agent._build_ceo_signal(
                "NXE", "CEO.ca/NXE", "ceo.ca_api", text,
                f"https://fixture.example/{identity}", stamp, text,
                source_record_id=identity,
            )

        future = row("future", "Assays returned 1.5% U3O8 across 8.0 m",
                     "2099-01-01T12:00:00Z")
        self.assertTrue(future["threshold_qualified"])
        fresh = row("fresh", "Uranium production update", "2026-11-25T11:00:00Z")
        agent._fetch_ceo_ca_channel = lambda *_args, **_kwargs: [future, fresh]
        agent._fetch_uranium_sector_news = lambda: []
        self.assertEqual(agent.get_ceo_ca_signals(), [fresh])
        dropped = agent.get_dropped_ceo_signals()
        self.assertEqual([(r["source_record_id"], r["drop_reason"]) for r in dropped],
                         [("future", "future")])
        self.assertEqual(agent.get_ceo_ca_health()["threshold_qualified_records"], 0)


if __name__ == "__main__":
    unittest.main()
