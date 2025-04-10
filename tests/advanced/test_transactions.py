from collections import Counter
from unittest.mock import patch

from pymongo import WriteConcern
from pymongo.errors import ConfigurationError, InvalidOperation, OperationFailure
from twisted.internet import defer, reactor
from twisted.trial import unittest

from tests.conf import MongoConf
from tests.mongod import create_mongod
from tests.utils import catch_sent_msgs
from txmongo import Database, MongoProtocol
from txmongo.collection import Collection
from txmongo.connection import ConnectionPool
from txmongo.sessions import TransactionOptions


class TestTransactions(unittest.TestCase):

    conf = MongoConf()
    port = conf.replicase_test_ports[0]
    rs_name = "rs1"

    __init_timeout = 60
    __ping_interval = 0.5

    conn: ConnectionPool
    db: Database
    coll: Collection

    @property
    def uri(self) -> str:
        return f"mongodb://localhost:{self.port}/?replicaSet={self.rs_name}"

    @property
    def uri_secondary_ok(self) -> str:
        # we use secondary-ok connection to set up the replicaset and to check its status
        return f"mongodb://localhost:{self.port}/?readPreference=secondaryPreferred"

    @property
    def rs_config(self) -> dict:
        return {
            "_id": self.rs_name,
            "members": [{"_id": 0, "host": f"localhost:{self.port}"}],
        }

    async def __check_reachable(self):
        conn = ConnectionPool(self.uri_secondary_ok)
        await conn.admin.command("ismaster", check=False)
        await conn.disconnect()

    def __sleep(self, delay):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, None)
        return d

    @defer.inlineCallbacks
    def setUp(self):
        self.__mongod = create_mongod(port=self.port, replset=self.rs_name)
        yield self.__mongod.start()
        yield defer.ensureDeferred(self.__check_reachable())
        conn = ConnectionPool(self.uri_secondary_ok)
        yield conn.admin.command("replSetInitiate", self.rs_config)

        n_tries = int(self.__init_timeout / self.__ping_interval)
        ok = False
        for i in range(n_tries):
            yield self.__sleep(self.__ping_interval)
            replset_status = yield conn.admin.command("replSetGetStatus", check=False)
            if (
                replset_status["ok"]
                and replset_status["members"][0]["stateStr"] == "PRIMARY"
            ):
                ok = True
                break

        try:
            if not ok:
                yield self.tearDown()
                raise Exception(
                    f"ReplicaSet initialization took more than {self.__init_timeout}s"
                )
        finally:
            yield conn.disconnect()

        self.conn = ConnectionPool(self.uri)
        self.db = self.conn.db
        self.coll = self.db.coll

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.conn.disconnect()
        yield self.__mongod.stop()

    async def test_commit_plain(self):
        session = self.conn.start_session()
        session.start_transaction()
        await self.coll.insert_one({"x": 1}, session=session)

        cnt_in_transaction = len(await self.coll.find(session=session))
        self.assertEqual(cnt_in_transaction, 1)

        cnt_outside_transaction = len(await self.coll.find())
        self.assertEqual(cnt_outside_transaction, 0)

        await session.commit_transaction()

        cnt_outside_transaction = len(await self.coll.find())
        self.assertEqual(cnt_outside_transaction, 1)

        await session.end_session()

    async def test_commit_context_manager(self):
        async with self.conn.start_session() as session:
            with catch_sent_msgs() as messages:
                async with session.start_transaction():
                    await self.coll.insert_one({"x": 1}, session=session)

                    cnt_in_transaction = len(await self.coll.find(session=session))
                    self.assertEqual(cnt_in_transaction, 1)

                    cnt_outside_transaction = len(await self.coll.find())
                    self.assertEqual(cnt_outside_transaction, 0)

        count_after_commit = len(await self.coll.find())
        self.assertEqual(count_after_commit, 1)

        [insert, find1, find2, commit] = messages
        self.assertIn("insert", insert.to_dict())
        self.assertIn("find", find1.to_dict())
        self.assertIn("find", find2.to_dict())
        self.assertIn("commitTransaction", commit.to_dict())

    async def test_abort_plain(self):
        session = self.conn.start_session()
        session.start_transaction()
        await self.coll.insert_one({"x": 1}, session=session)

        cnt_in_transaction = len(await self.coll.find(session=session))
        self.assertEqual(cnt_in_transaction, 1)

        await session.abort_transaction()

        cnt_outside_transaction = len(await self.coll.find())
        self.assertEqual(cnt_outside_transaction, 0)

        await session.end_session()

    async def test_abort_by_end_session(self):
        with catch_sent_msgs() as messages:
            async with self.conn.start_session() as session:
                session.start_transaction()
                await self.coll.insert_one({"x": 1}, session=session)

        count = len(await self.coll.find())
        self.assertEqual(count, 0)
        self.assertTrue(any("abortTransaction" in msg.to_dict() for msg in messages))

    async def test_abort_by_exception(self):
        try:
            async with self.conn.start_session() as session:
                with catch_sent_msgs() as messages:
                    async with session.start_transaction():
                        await self.coll.insert_one({"x": 1}, session=session)

                        count = len(await self.coll.find(session=session))
                        self.assertEqual(count, 1)

                        raise ZeroDivisionError("Boom")
        except ZeroDivisionError:
            pass

        count = len(await self.coll.find())
        self.assertEqual(count, 0)

        [insert, find, abort] = messages
        self.assertIn("insert", insert.to_dict())
        self.assertIn("find", find.to_dict())
        self.assertIn("abortTransaction", abort.to_dict())

    async def test_ignore_write_concern(self):
        """Driver must ignore write concern on operations in transaction and only send WC with commit/abort_transaction"""
        async with self.conn.start_session() as session:
            async with session.start_transaction():
                with catch_sent_msgs() as messages:
                    coll_wc = self.coll.with_options(write_concern=WriteConcern(w=1))
                    await coll_wc.insert_one({"x": 1}, session=session)

        [insert] = messages
        self.assertNotIn("writeConcern", insert.to_dict())

    def test_no_unacknowledged(self):
        """Unacknowledged WC is not supported by start_transaction"""
        session = self.conn.start_session()
        with self.assertRaises(ConfigurationError):
            session.start_transaction(write_concern=WriteConcern(w=0))

    async def test_commit_write_concern(self):
        """WC from transaction options is sent along with commit_transaction"""
        async with self.conn.start_session() as session:
            with catch_sent_msgs() as messages:
                async with session.start_transaction(
                    write_concern=WriteConcern(w=1, wtimeout=123)
                ):
                    await self.coll.insert_one({"x": 1}, session=session)

        [insert, commit] = messages
        self.assertNotIn("writeConcern", insert.to_dict())
        self.assertIn("commitTransaction", commit.to_dict())
        self.assertEqual(commit.to_dict()["writeConcern"], {"w": 1, "wtimeout": 123})

    async def test_inherit_conn_write_concern(self):
        conn = ConnectionPool(self.uri + f"&w=majority&wtimeoutMS=1234")
        try:
            async with conn.start_session() as session:
                with catch_sent_msgs() as messages:
                    async with session.start_transaction():
                        await conn.db.coll.insert_one({"x": 1}, session=session)

            [_, commit] = messages
            wc = commit.to_dict()["writeConcern"]
            self.assertEqual(wc, {"w": "majority", "wtimeout": 1234})
        finally:
            await conn.disconnect()

    async def test_no_default_transaction_options(self):
        async with self.conn.start_session() as session:
            with catch_sent_msgs() as messages:
                async with session.start_transaction():
                    await self.coll.insert_one({"x": 1}, session=session)

        [_, commit] = messages
        commit_msg = commit.to_dict()
        self.assertEqual(commit_msg["writeConcern"], {})
        self.assertNotIn("maxTimeMS", commit_msg)

    async def test_default_transaction_options(self):
        async with self.conn.start_session(
            default_transaction_options=TransactionOptions(
                write_concern=WriteConcern(w="majority", wtimeout=987),
                max_commit_time_ms=12345,
            )
        ) as session:
            with catch_sent_msgs() as messages:
                async with session.start_transaction():
                    await self.coll.insert_one({"x": 1}, session=session)

        [_, commit] = messages
        commit_cmd = commit.to_dict()
        self.assertEqual(commit_cmd["writeConcern"], {"w": "majority", "wtimeout": 987})
        self.assertEqual(commit_cmd["maxTimeMS"], 12345)

    async def test_transaction_options_validation(self):
        async with self.conn.start_session() as session:
            with self.assertRaises(TypeError):
                session.start_transaction(write_concern=123)
            with self.assertRaises(TypeError):
                session.start_transaction(write_concern={"w": 1})
            with self.assertRaises(ConfigurationError):
                session.start_transaction(write_concern=WriteConcern(w=0))
            with self.assertRaises(TypeError):
                session.start_transaction(max_commit_time_ms="5")

    async def test_abort_write_concern(self):
        """WC from transaction options is sent along with commit_transaction"""
        try:
            async with self.conn.start_session() as session:
                with catch_sent_msgs() as messages:
                    async with session.start_transaction(
                        write_concern=WriteConcern(w=1, wtimeout=123)
                    ):
                        await self.coll.insert_one({"x": 1}, session=session)
                        raise NotImplementedError()
        except NotImplementedError:
            pass

        [insert, abort] = messages
        self.assertNotIn("writeConcern", insert.to_dict())
        self.assertIn("abortTransaction", abort.to_dict())
        self.assertEqual(abort.to_dict()["writeConcern"], {"w": 1, "wtimeout": 123})

    async def test_max_commit_time_ms(self):
        async with self.conn.start_session() as session:
            with catch_sent_msgs() as messages:
                async with session.start_transaction(max_commit_time_ms=1234):
                    await self.coll.insert_one({"x": 1}, session=session)

        [_, commit] = messages
        self.assertIn("commitTransaction", commit.to_dict())
        self.assertEqual(commit.to_dict()["maxTimeMS"], 1234)

    async def test_retry(self):

        def get_fake_send_msg():
            orig_send_raw_msg = MongoProtocol._send_raw_msg
            msg_no = 0

            def fake_send_msg(self, msg):
                nonlocal msg_no
                msg_no += 1
                if msg_no == 2:
                    raise OperationFailure(
                        "BOOM", 262, details={"errorLabels": ["RetryableWriteError"]}
                    )

                return orig_send_raw_msg(self, msg)

            return fake_send_msg

        async with self.conn.start_session() as session:
            with patch.object(
                MongoProtocol,
                "_send_raw_msg",
                side_effect=get_fake_send_msg(),
                autospec=True,
            ):
                with catch_sent_msgs() as messages:
                    session.start_transaction(write_concern=WriteConcern(w=1))
                    await self.coll.insert_one({"x": 1}, session=session)
                    await session.commit_transaction()

        self.assertEqual(len(messages), 3)
        self.assertIn("insert", messages[0].to_dict())
        self.assertIn("commitTransaction", messages[1].to_dict())
        self.assertIn("commitTransaction", messages[2].to_dict())
        self.assertEqual(messages[1].to_dict()["writeConcern"], {"w": 1})
        self.assertEqual(
            messages[2].to_dict()["writeConcern"],
            {"w": "majority", "wtimeout": 10000},
        )

    async def test_transaction_on_ended_session(self):
        async with self.conn.start_session() as session:
            await self.coll.insert_one({"x": 1}, session=session)

        with self.assertRaises(InvalidOperation):
            session.start_transaction()

        with self.assertRaises(InvalidOperation):
            await session.abort_transaction()

    async def test_transaction_already_active(self):
        async with self.conn.start_session() as session:
            async with session.start_transaction():
                with self.assertRaises(InvalidOperation):
                    session.start_transaction()

    async def test_abort_no_transaction(self):
        async with self.conn.start_session() as session:
            with self.assertRaises(InvalidOperation):
                await session.abort_transaction()

    async def test_abort_after_commit(self):
        async with self.conn.start_session() as session:
            async with session.start_transaction():
                await self.coll.insert_one({"x": 1}, session=session)
            with self.assertRaises(InvalidOperation):
                await session.abort_transaction()

    async def test_commit_after_abort(self):
        async with self.conn.start_session() as session:
            session.start_transaction()
            await self.coll.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
            with self.assertRaises(InvalidOperation):
                await session.commit_transaction()

    async def test_empty_commit(self):
        with catch_sent_msgs() as messages:
            async with self.conn.start_session() as session:
                async with session.start_transaction():
                    pass

        self.assertEqual(len(messages), 0)

    async def test_commit_without_start(self):
        async with self.conn.start_session() as session:
            with self.assertRaises(InvalidOperation):
                await session.commit_transaction()

    async def test_empty_abort(self):
        with catch_sent_msgs() as messages:
            async with self.conn.start_session() as session:
                session.start_transaction()
                await session.abort_transaction()

        self.assertEqual(len(messages), 0)

    async def test_multiple_commit(self):
        with catch_sent_msgs() as messages:
            async with self.conn.start_session() as session:
                session.start_transaction()
                await self.coll.insert_one({"x": 1}, session=session)
                await session.commit_transaction()
                await session.commit_transaction()
                await session.commit_transaction()

        cmd_count = Counter(list(msg.to_dict().keys())[0] for msg in messages)
        self.assertEqual(cmd_count, {"insert": 1, "commitTransaction": 3})

    async def test_multiple_abort(self):
        async with self.conn.start_session() as session:
            session.start_transaction()
            await self.coll.insert_one({"x": 1}, session=session)
            await session.abort_transaction()
            with self.assertRaises(InvalidOperation):
                await session.abort_transaction()

    async def test_count_documents(self):
        async with self.conn.start_session() as session:
            async with session.start_transaction():
                await self.coll.insert_one({"x": 1}, session=session)
                self.assertEqual(
                    await self.coll.count_documents({}, session=session), 1
                )
                self.assertEqual(await self.coll.count_documents({}), 0)

        self.assertEqual(await self.coll.count_documents({}), 1)
