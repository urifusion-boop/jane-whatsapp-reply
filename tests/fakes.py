"""Minimal in-memory Motor-compatible fake, scoped to exactly what
agent_reply_service.py/conversation_service.py need for unit tests. No mongomock
dependency in this repo's requirements.txt — kept deliberately small rather than
adding one for a handful of collections/operations.

Supports: find_one, insert_one (with unique-index simulation), update_one ($set/
$unset), delete_one, count_documents, find().sort() as an async iterator. Not a
general-purpose Mongo simulator — extend narrowly if a new test needs one more
operator, don't grow this into a second ORM.
"""

import copy
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, key, direction=1):
        self._docs = sorted(self._docs, key=lambda d: d.get(key), reverse=(direction == -1))
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return copy.deepcopy(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, unique_fields: Optional[List[str]] = None):
        self._docs: Dict[Any, Dict[str, Any]] = {}
        self._unique_fields = unique_fields or []

    def _matches(self, doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
                for op, val in expected.items():
                    if op == "$ne" and actual == val:
                        return False
                    if op == "$lt" and not (actual is not None and actual < val):
                        return False
                    if op == "$exists" and (key in doc) != val:
                        return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for doc in self._docs.values():
            if self._matches(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query: Optional[Dict[str, Any]] = None) -> FakeCursor:
        query = query or {}
        return FakeCursor([d for d in self._docs.values() if self._matches(d, query)])

    async def count_documents(self, query: Dict[str, Any]) -> int:
        return len([d for d in self._docs.values() if self._matches(d, query)])

    async def insert_one(self, doc: Dict[str, Any]):
        doc = copy.deepcopy(doc)
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        for field in self._unique_fields:
            if field in doc and any(d.get(field) == doc[field] for d in self._docs.values()):
                raise DuplicateKeyError(f"duplicate key on {field}")
        self._docs[doc["_id"]] = doc
        return type("InsertResult", (), {"inserted_id": doc["_id"]})()

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        modified = 0
        for doc in self._docs.values():
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        doc.pop(key, None)
                modified += 1
                break  # update_one semantics — only the first match
        return type("UpdateResult", (), {"modified_count": modified})()

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any]):
        modified = 0
        for doc in self._docs.values():
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        doc.pop(key, None)
                modified += 1
        return type("UpdateResult", (), {"modified_count": modified})()

    async def delete_one(self, query: Dict[str, Any]):
        for _id, doc in list(self._docs.items()):
            if self._matches(doc, query):
                del self._docs[_id]
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()

    async def create_index(self, *args, **kwargs):
        return "fake-index"


class FakeDatabase:
    UNIQUE_FIELDS = {
        "jane_wa_processed_messages": ["message_id"],
        "jane_wa_agent_reply_claims": ["idempotency_key"],
        "jane_wa_conversations": [],  # compound unique not simulated — not exercised by these tests
    }

    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(unique_fields=self.UNIQUE_FIELDS.get(name, []))
        return self._collections[name]
