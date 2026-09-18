-- Exchange inference here is restricted to the stock/ETF domain. Index codes
-- (e.g. sh000300) are mapped by their explicitly configured exchange.
CREATE OR REPLACE MACRO core.canonical_symbol(s) AS
 CASE
 WHEN regexp_full_match(upper(s),'[0-9]{6}\.(SH|SS|SZ|BJ)') THEN replace(upper(s),'.SS','.SH')
 WHEN regexp_full_match(upper(s),'(SH|SZ|BJ)\.?[0-9]{6}') THEN right(s,6)||'.'||left(upper(s),2)
 WHEN regexp_full_match(s,'[0-9]{6}') THEN s||CASE
   WHEN left(s,1) IN ('5','6') THEN '.SH'
   WHEN left(s,1) IN ('0','1','2','3') THEN '.SZ'
   WHEN left(s,1) IN ('4','8','9') THEN '.BJ' END
 ELSE NULL END;

CREATE OR REPLACE VIEW core.bar_candidates AS
WITH mapped AS (
 SELECT b.*,core.canonical_symbol(b.symbol) canonical_symbol,
 coalesce(json_extract_string(extra,'$.price_adjustment'),
   CASE json_extract_string(extra,'$.adjustflag') WHEN '1' THEN 'hfq' WHEN '2' THEN 'qfq' WHEN '3' THEN 'raw' END,
   c.adjustment,'unknown') price_adjustment,
 c.volume_multiplier,c.volume_unit,c.amount_unit,c.asset_type,c.priority
 FROM raw.equity_daily_bars b LEFT JOIN core.source_contracts c USING(source_id)
)
SELECT md5(canonical_symbol) instrument_id,canonical_symbol symbol,asset_type,
 cast(obs_time AS DATE) trade_date,'1d' frequency,open,high,low,close,
 volume*volume_multiplier volume,amount,turnover_rate,NULL::DOUBLE open_interest,
 price_adjustment,'CNY' currency,volume_unit,amount_unit,'percent' turnover_rate_unit,source_id,ingested_at,extra,priority,
 CASE WHEN canonical_symbol IS NULL THEN 'unmapped_symbol'
 WHEN price_adjustment NOT IN ('raw','qfq','hfq','total_return') THEN 'unknown_adjustment'
 WHEN volume_unit IS NULL THEN 'unknown_units'
 WHEN obs_time IS NULL OR obs_time <> date_trunc('day',obs_time) OR obs_time::DATE > current_date THEN 'invalid_date'
 WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 'missing_ohlc'
 WHEN NOT (isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close)) THEN 'nonfinite_price'
 WHEN least(open,high,low,close)<0 OR volume<0 OR amount<0 OR turnover_rate<0 THEN 'negative_value'
 WHEN (volume IS NOT NULL AND NOT isfinite(volume)) OR (amount IS NOT NULL AND NOT isfinite(amount)) OR (turnover_rate IS NOT NULL AND NOT isfinite(turnover_rate)) THEN 'nonfinite_quantity'
 WHEN high<greatest(open,low,close) OR low>least(open,high,close) THEN 'invalid_ohlc'
 ELSE NULL END quality_issue
FROM mapped;

CREATE OR REPLACE TABLE core.market_daily_bars_by_source AS
SELECT * EXCLUDE (quality_issue,priority) FROM core.bar_candidates
WHERE quality_issue IS NULL
QUALIFY row_number() OVER (PARTITION BY instrument_id,trade_date,frequency,price_adjustment,source_id
 ORDER BY ingested_at DESC,open,high,low,close,volume NULLS LAST)=1;

CREATE OR REPLACE TABLE core.market_daily_bars AS
SELECT b.* FROM core.market_daily_bars_by_source b
LEFT JOIN core.source_contracts c USING(source_id)
LEFT JOIN raw.selected_series selected ON selected.symbol=split_part(b.symbol,'.',1)
 AND selected.price_adjustment=b.price_adjustment AND selected.source_id=b.source_id
QUALIFY row_number() OVER(PARTITION BY b.instrument_id,b.trade_date,b.frequency,b.price_adjustment
 ORDER BY (selected.source_id IS NOT NULL) DESC,c.priority NULLS LAST,b.ingested_at DESC,b.source_id)=1;

CREATE OR REPLACE TABLE core.instrument_master AS
SELECT md5(symbol) instrument_id,symbol,max(name) AS name,asset_type,right(symbol,2) exchange,'CNY' currency,
 NULL::DATE list_date,NULL::DATE delist_date,'unknown' status,current_timestamp created_at,current_timestamp updated_at
FROM (
 SELECT DISTINCT core.canonical_symbol(b.symbol) symbol,NULL::VARCHAR AS name,c.asset_type
 FROM raw.equity_daily_bars b JOIN core.source_contracts c USING(source_id)
 UNION ALL
 SELECT core.canonical_symbol(symbol),name,'etf' FROM raw.etf_share_daily
) WHERE symbol IS NOT NULL GROUP BY symbol,asset_type;
CREATE UNIQUE INDEX IF NOT EXISTS instrument_id_unique ON core.instrument_master(instrument_id);

CREATE OR REPLACE TABLE core.instrument_identifiers AS
SELECT DISTINCT md5(core.canonical_symbol(symbol)) instrument_id,symbol identifier,'provider_code' identifier_type,
 source_id,NULL::DATE valid_from,NULL::DATE valid_to FROM raw.equity_daily_bars
WHERE core.canonical_symbol(symbol) IS NOT NULL
UNION
SELECT DISTINCT md5(core.canonical_symbol(symbol)),symbol,'provider_code',source_id,NULL::DATE,NULL::DATE
FROM raw.etf_share_daily WHERE core.canonical_symbol(symbol) IS NOT NULL;

CREATE OR REPLACE TABLE core.etf_daily_facts AS
SELECT md5(core.canonical_symbol(e.symbol)) instrument_id,obs_time::DATE trade_date,
 shares,share_change,close,estimated_flow_amount,source_id,ingested_at,
 'provider_reported_unverified' shares_unit,'CNY' amount_unit
FROM raw.etf_share_daily e LEFT JOIN main.sources s USING(source_id)
WHERE shares>=0 AND isfinite(shares) AND obs_time IS NOT NULL
AND core.canonical_symbol(e.symbol) IS NOT NULL
QUALIFY row_number() OVER(PARTITION BY instrument_id,trade_date
 ORDER BY s.priority NULLS LAST,ingested_at DESC,source_id)=1;

CREATE OR REPLACE TABLE core.observations_by_source AS SELECT * REPLACE (
 CASE unit WHEN '浜垮厓' THEN '亿元' WHEN '鍏?' THEN '元' ELSE unit END AS unit
) FROM raw.observations
WHERE obs_time IS NOT NULL AND value IS NOT NULL AND isfinite(value)
QUALIFY row_number() OVER(PARTITION BY indicator_id,series_id,coalesce(unit,''),obs_time,source_id
 ORDER BY ingested_at DESC)=1;
CREATE OR REPLACE TABLE core.observations AS
SELECT o.* FROM core.observations_by_source o LEFT JOIN main.sources s USING(source_id)
QUALIFY row_number() OVER(PARTITION BY indicator_id,series_id,coalesce(unit,''),obs_time
 ORDER BY s.priority NULLS LAST,ingested_at DESC,source_id)=1;
CREATE OR REPLACE TABLE core.other_market_daily_bars_by_source AS
SELECT * FROM raw.market_daily_bars
WHERE obs_time IS NOT NULL AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
AND isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close)
AND high>=greatest(open,low,close) AND low<=least(open,high,close)
QUALIFY row_number() OVER(PARTITION BY asset_id,obs_time,source_id ORDER BY ingested_at DESC)=1;
CREATE OR REPLACE TABLE core.other_market_daily_bars AS
SELECT b.* FROM core.other_market_daily_bars_by_source b LEFT JOIN main.sources s USING(source_id)
QUALIFY row_number() OVER(PARTITION BY asset_id,obs_time
 ORDER BY s.priority NULLS LAST,ingested_at DESC,source_id)=1;
CREATE OR REPLACE VIEW core.sources AS SELECT * FROM main.sources;
CREATE OR REPLACE VIEW core.data_quality_issues AS
 SELECT instrument_id,source_id,trade_date,quality_issue FROM core.bar_candidates WHERE quality_issue IS NOT NULL;

-- Stable scalar interface; legacy-shaped core.observations remains available to
-- existing readers while new research code uses observation_date.
CREATE OR REPLACE TABLE core.macro_observations AS
SELECT series_id,indicator_id,source_id,obs_time::DATE observation_date,value,unit,ingested_at
FROM core.observations WHERE obs_time IS NOT NULL AND value IS NOT NULL AND isfinite(value)
QUALIFY row_number() OVER(PARTITION BY indicator_id,series_id,obs_time,unit ORDER BY ingested_at DESC)=1;
