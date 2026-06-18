"""Structural tests for the QueryMixin seam.

QueryMixin was extracted from AgentTracker to group the backend-backed
query/export methods (compare/export/costs/pricing/search/heatmap) alongside
the other tracker mixins (Alert/Tag/Annotation/Retention). These tests pin the
seam itself: the mixin is an independent class, it contributes exactly the
expected methods, and those methods remain reachable on AgentTracker via MRO
with identity preserved (i.e. AgentTracker did not shadow them with its own
copies). The behavioral coverage for each method lives in test_tracker.py.
"""

from agentlens.tracker import AgentTracker
from agentlens.tracker_queries import QueryMixin

QUERY_METHODS = (
    "compare_sessions",
    "export_session",
    "get_costs",
    "get_pricing",
    "set_pricing",
    "search_events",
    "heatmap",
)


class TestQueryMixinSeam:
    def test_querymixin_is_in_tracker_mro(self):
        assert QueryMixin in AgentTracker.__mro__

    def test_querymixin_is_independent_of_agenttracker(self):
        # The mixin must not depend on AgentTracker (one-directional seam):
        # it carries its own methods and does not subclass the tracker.
        assert not issubclass(QueryMixin, AgentTracker)

    def test_querymixin_defines_exactly_the_query_methods(self):
        own = {
            name
            for name, val in vars(QueryMixin).items()
            if callable(val) and not name.startswith("__")
        }
        assert own == set(QUERY_METHODS)

    def test_query_methods_resolve_to_querymixin_not_tracker(self):
        # Each query method on AgentTracker must be the one defined on the
        # mixin -- proving the methods were moved, not duplicated.
        for name in QUERY_METHODS:
            assert getattr(AgentTracker, name) is getattr(QueryMixin, name), name

    def test_tracker_does_not_redefine_query_methods(self):
        tracker_own = set(vars(AgentTracker))
        for name in QUERY_METHODS:
            assert name not in tracker_own, f"AgentTracker should not redefine {name}"

    def test_core_capture_methods_stay_on_agenttracker(self):
        # Sanity guard: the extraction must not have pulled core capture/local
        # methods (which read tracker state) into the query seam.
        for name in ("track", "track_tool", "start_session", "end_session",
                     "span", "explain", "timeline", "health_score"):
            assert name in vars(AgentTracker), name
            assert name not in vars(QueryMixin), name
