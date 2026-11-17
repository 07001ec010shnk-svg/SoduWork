"""API timestamps must carry a UTC offset even when SQLite returns naive values."""
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.memory_record_models import MemoryRecord
from app.models import Base
from app.services import memory_records
from app.timestamps import iso_utc


class IsoUtcTests(unittest.TestCase):
    def test_naive_values_are_utc(self):
        self.assertEqual(iso_utc(datetime(2026, 11, 17, 9, 24, 39)), "2026-11-17T09:24:39+00:00")

    def test_aware_values_keep_their_instant(self):
        beijing = datetime(2026, 11, 17, 17, 24, 39, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(iso_utc(beijing), "2026-11-17T09:24:39+00:00")

    def test_none(self):
        self.assertIsNone(iso_utc(None))


class SqliteRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_record_read_back_from_sqlite_has_an_offset(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(
                sync, tables=[MemoryRecord.__table__]))
        stamp = datetime(2026, 11, 17, 9, 24, 39, tzinfo=timezone.utc)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(MemoryRecord(id="m1", scope="personal", owner_user_id="u1", memory_key="k",
                                     title="t", created_at=stamp, updated_at=stamp))
            await session.commit()
        async with sessions() as session:
            row = await session.get(MemoryRecord, "m1")
            # This is the condition the serializers must handle.
            self.assertIsNone(row.created_at.tzinfo)
            view = memory_records.view(row)
        self.assertEqual(view["createdAt"], "2026-11-17T09:24:39+00:00")
        self.assertEqual(view["updatedAt"], "2026-11-17T09:24:39+00:00")


if __name__ == "__main__":
    unittest.main()
