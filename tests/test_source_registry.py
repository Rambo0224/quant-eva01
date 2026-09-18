from datahub.sync.source_registry import validate_market_sources
from datahub.sync.storage import settings


def test_every_market_candidate_is_a_bound_source_module():
    validate_market_sources(settings())
