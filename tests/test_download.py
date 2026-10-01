import io
import lzma
from datetime import date

from bot import download_dukascopy as dk


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_day_decodes_bi5(monkeypatch):
    records = b"".join([
        dk.RECORD.pack(0, 110000, 110010, 109990, 110020, 12.5),      # 00:00
        dk.RECORD.pack(60, 110010, 110005, 110000, 110015, 3.0),      # 00:01
        dk.RECORD.pack(120, 110005, 110005, 110005, 110005, 0.0),     # filler, dropped
    ])
    blob = lzma.compress(records, format=lzma.FORMAT_ALONE)
    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return FakeResponse(blob)

    monkeypatch.setattr(dk.urllib.request, "urlopen", fake_urlopen)
    df = dk.fetch_day("EURUSD", date(2024, 3, 5))
    assert seen["url"].endswith("/EURUSD/2024/02/05/BID_candles_min_1.bi5")   # month is 0-based
    assert len(df) == 2
    first = df.iloc[0]
    assert [round(v, 5) for v in (first.open, first.high, first.low, first.close)] == [1.1, 1.1002, 1.0999, 1.1001]
    assert str(df["time"].iat[1]) == "2024-03-05 00:01:00"
