#!/usr/bin/env python3
from macro_replay.config import find_indicator, load_indicators
from macro_replay.pipeline import process_indicator

indicator = next(i for i in load_indicators() if i.id == "usd-breakeven")
process_indicator(indicator)
