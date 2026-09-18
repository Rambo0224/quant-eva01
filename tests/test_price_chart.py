from dataclasses import replace
from app.analysis.engine import analyze
from test_quant_analysis import sample


def test_close_line_available_without_ohlc_and_respects_dates():
    data = sample(length=60)
    data.frame = data.frame[['close']]
    data.dataset = replace(data.dataset, fields=['close'])
    start, end = [data.frame.index[i].strftime('%Y-%m-%d') for i in (2, 40)]
    result = analyze(data, 'ma', None, start, end)
    assert len(result['history']) == 39
    assert result['history'][0]['input'] == data.frame.close.iloc[2]
    assert result['history'][-1]['date'] == end
    assert result['price_chart']['is_price'] is True
    assert 'bars' not in result['price_chart']
    assert result['price_chart']['adjustment'] == data.dataset.adjustment


def test_non_price_series_is_labelled_as_original_data():
    data = sample()
    data.dataset = replace(data.dataset, price=False)
    result = analyze(data,'ma',None,data.dataset.first_date,data.dataset.last_date)
    assert result['price_chart']['is_price'] is False
