import unittest
import warnings
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_database_pool
from app.core.security import create_access_token
from app.main import app

_NOW = datetime(2026, 7, 7, tzinfo=UTC)
_TODAY = date(2026, 7, 7)


class FakeConnection:
    """커뮤니티 라우트용 인메모리 가짜 DB (실제 SQL 미실행 — 라우트/리포지토리 로직 검증용).

    실제 SQL 정합성은 별도 격리 DB 스모크로 검증한다(faked 테스트로는 못 잡음).
    """

    def __init__(self):
        self.users_by_id = {
            1: {"id": 1, "email": "a@example.com", "member_code": "AAAA2345", "nickname": "글쓴이",
                "agreed_risk": True, "is_verified": False},
            2: {"id": 2, "email": "b@example.com", "member_code": "BBBB3456", "nickname": "구경꾼",
                "agreed_risk": True, "is_verified": False},
        }
        # journal_id -> 소유/화이트리스트 필드
        self.journals = {
            20: {"user_id": 1, "stock_id": 10, "user_view": "watch", "user_memo": "삼성 관찰 중",
                 "outcome_change_pct": Decimal("5.00")},
            30: {"user_id": 2, "stock_id": 10, "user_view": "research_more", "user_memo": "메모",
                 "outcome_change_pct": Decimal("-3.00")},
        }
        self.stocks = {10: {"ticker": "005930", "name": "삼성전자"}}
        self.posts = []
        self.next_post_id = 100
        self.comments = []
        self.next_comment_id = 500
        self.reactions = []
        self.next_reaction_id = 900
        self.reports = []
        self.next_report_id = 1300
        self.views = set()

    # ---- 조인 행 구성 ----
    def _post_row(self, post):
        j = self.journals.get(post["journal_id"]) if post["journal_id"] is not None else None
        stock = self.stocks.get(j["stock_id"]) if j else None
        author = self.users_by_id[post["author_user_id"]]
        like_count = sum(
            1 for r in self.reactions
            if r["target_type"] == "post" and r["target_id"] == post["id"] and r["type"] == "like"
        )
        comment_count = sum(
            1 for c in self.comments
            if c["post_id"] == post["id"] and c["deleted_at"] is None and c["status"] == "visible"
        )
        return {
            **post,
            "author_member_code": author["member_code"],
            "author_nickname": author["nickname"],
            "journal_user_view": j["user_view"] if j else None,
            "journal_user_memo": j["user_memo"] if j else None,
            "stock_ticker": stock["ticker"] if stock else None,
            "stock_name": stock["name"] if stock else None,
            "pnl_pct": (j["outcome_change_pct"] if (j and post["show_pnl"]) else None),
            "like_count": like_count,
            "comment_count": comment_count,
        }

    def _comment_row(self, comment):
        author = self.users_by_id[comment["author_user_id"]]
        return {**comment, "author_member_code": author["member_code"],
                "author_nickname": author["nickname"]}

    def _visible(self, post):
        return post["deleted_at"] is None and post["status"] == "visible"

    # ---- asyncpg 인터페이스 ----
    async def fetchval(self, sql, *args):
        if "FROM signal_journals WHERE id = $1" in sql:
            j = self.journals.get(args[0])
            return j["user_id"] if j else None
        if "INSERT INTO community_posts" in sql:
            post = {"id": self.next_post_id, "author_user_id": args[0], "journal_id": args[1],
                    "title": args[2], "body": args[3], "show_pnl": args[4], "view_count": 0,
                    "status": "visible", "deleted_at": None, "created_at": _NOW, "updated_at": _NOW}
            self.next_post_id += 1
            self.posts.append(post)
            return post["id"]
        if "INSERT INTO community_post_views" in sql:
            key = (args[0], args[1], _TODAY)
            if key in self.views:
                return None
            self.views.add(key)
            return 1
        if "INSERT INTO community_comments" in sql:
            post_id, parent_id, author_id, body = args
            post = self._find_post(post_id)
            if post is None or not self._visible(post):
                return None
            if parent_id is not None:
                parent = next((c for c in self.comments if c["id"] == parent_id), None)
                if (parent is None or parent["post_id"] != post_id
                        or parent["parent_comment_id"] is not None or parent["deleted_at"] is not None):
                    return None
            comment = {"id": self.next_comment_id, "post_id": post_id, "parent_comment_id": parent_id,
                       "author_user_id": author_id, "body": body, "status": "visible",
                       "deleted_at": None, "created_at": _NOW}
            self.next_comment_id += 1
            self.comments.append(comment)
            return comment["id"]
        if "SELECT author_user_id FROM community_posts" in sql:
            post = self._find_post(args[0])
            return post["author_user_id"] if (post and post["deleted_at"] is None) else None
        if "SELECT author_user_id FROM community_comments" in sql:
            c = next((c for c in self.comments if c["id"] == args[0] and c["deleted_at"] is None), None)
            return c["author_user_id"] if c else None
        if "UPDATE community_posts" in sql and "SET title = COALESCE" in sql:
            post = self._find_post(args[0])
            if post is None or post["author_user_id"] != args[1] or post["deleted_at"] is not None:
                return None
            if args[2] is not None:
                post["title"] = args[2]
            if args[3] is not None:
                post["body"] = args[3]
            if args[4] is not None:
                post["show_pnl"] = args[4]
            post["updated_at"] = _NOW
            return post["id"]
        if "UPDATE community_posts SET deleted_at" in sql:
            post = self._find_post(args[0])
            if post is None or post["author_user_id"] != args[1] or post["deleted_at"] is not None:
                return None
            post["deleted_at"] = _NOW
            return post["id"]
        if "UPDATE community_posts SET status = 'hidden'" in sql:
            post = self._find_post(args[0])
            if post is None or post["status"] != "visible":
                return None
            post["status"] = "hidden"
            return post["id"]
        if "UPDATE community_comments SET deleted_at" in sql:
            c = next((c for c in self.comments if c["id"] == args[0]), None)
            if c is None or c["author_user_id"] != args[1] or c["deleted_at"] is not None:
                return None
            c["deleted_at"] = _NOW
            return c["id"]
        if "UPDATE community_comments SET status = 'hidden'" in sql:
            c = next((c for c in self.comments if c["id"] == args[0]), None)
            if c is None or c["status"] != "visible":
                return None
            c["status"] = "hidden"
            return c["id"]
        if "DELETE FROM community_reactions" in sql:
            user_id, target_type, target_id, rtype = args
            match = next((r for r in self.reactions if r["user_id"] == user_id
                          and r["target_type"] == target_type and r["target_id"] == target_id
                          and r["type"] == rtype), None)
            if match is None:
                return None
            self.reactions.remove(match)
            return match["id"]
        if "count(DISTINCT reporter_user_id)" in sql:
            return len({r["reporter_user_id"] for r in self.reports
                        if r["target_type"] == args[0] and r["target_id"] == args[1]})
        if "count(*) FROM community_reactions" in sql:
            return sum(1 for r in self.reactions if r["target_type"] == args[0]
                       and r["target_id"] == args[1] and r["type"] == args[2])
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM community_posts" in sql:  # get_post (서브쿼리에 comments/reactions 있어도 메인은 posts)
            post = self._find_post(args[0])
            if post is None or not self._visible(post):
                return None
            return self._post_row(post)
        if "FROM community_comments" in sql:  # create_comment 재조회
            c = next((c for c in self.comments if c["id"] == args[0]), None)
            return self._comment_row(c) if c else None
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "community_post_rankings" in sql:  # popular
            return []
        if "FROM community_posts" in sql:  # feed
            cursor_id, limit = args
            rows = sorted((p for p in self.posts if self._visible(p)), key=lambda p: p["id"], reverse=True)
            if cursor_id is not None:
                rows = [p for p in rows if p["id"] < cursor_id]
            return [self._post_row(p) for p in rows[:limit]]
        if "FROM community_comments" in sql:  # list_comments
            rows = [c for c in self.comments if c["post_id"] == args[0]
                    and c["deleted_at"] is None and c["status"] == "visible"]
            return [self._comment_row(c) for c in sorted(rows, key=lambda c: c["id"])]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def execute(self, sql, *args):
        if "UPDATE community_posts SET view_count = view_count + 1" in sql:
            post = self._find_post(args[0])
            if post is not None:
                post["view_count"] += 1
            return "UPDATE 1"
        if "INSERT INTO community_reactions" in sql:
            user_id, target_type, target_id, rtype = args
            exists = any(r["user_id"] == user_id and r["target_type"] == target_type
                         and r["target_id"] == target_id and r["type"] == rtype for r in self.reactions)
            if not exists:
                self.reactions.append({"id": self.next_reaction_id, "user_id": user_id,
                                       "target_type": target_type, "target_id": target_id, "type": rtype})
                self.next_reaction_id += 1
            return "INSERT 0 1"
        if "INSERT INTO community_reports" in sql:
            reporter_id, target_type, target_id, reason = args
            exists = any(r["reporter_user_id"] == reporter_id and r["target_type"] == target_type
                         and r["target_id"] == target_id for r in self.reports)
            if not exists:
                self.reports.append({"id": self.next_report_id, "reporter_user_id": reporter_id,
                                     "target_type": target_type, "target_id": target_id, "reason": reason})
                self.next_report_id += 1
            return "INSERT 0 1"
        raise AssertionError(f"Unexpected execute SQL: {sql}")

    def _find_post(self, post_id):
        return next((p for p in self.posts if p["id"] == post_id), None)


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class CommunityRoutesTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(self.connection)
        self.client = TestClient(app)
        self.token1 = self.token_for(1, "a@example.com")
        self.token2 = self.token_for(2, "b@example.com")

    def tearDown(self):
        app.dependency_overrides.clear()

    def token_for(self, user_id, email):
        return create_access_token(
            user_id=user_id, email=email,
            secret_key=get_settings().auth_secret_key, expires_delta=timedelta(minutes=30),
        )

    def headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def create_post(self, token, **overrides):
        payload = {"title": "삼성 판단 공유", "body": "이렇게 봤습니다", "journal_id": 20, "show_pnl": False}
        payload.update(overrides)
        return self.client.post("/api/community/posts", json=payload, headers=self.headers(token))

    # ---- 읽기 공개 ----
    def test_feed_is_public_without_auth(self):
        self.create_post(self.token1)
        response = self.client.get("/api/community/posts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)

    def test_feed_excludes_pnl_unless_opted_in(self):
        self.create_post(self.token1, show_pnl=False)
        item = self.client.get("/api/community/posts").json()["items"][0]
        self.assertIsNone(item["journal"]["pnl_pct"])  # 손익 기본 비공개(NFR-1)
        self.assertEqual(item["journal"]["stock"]["ticker"], "005930")  # 종목은 공개
        self.assertEqual(item["author"]["nickname"], "글쓴이")
        self.assertNotIn("author_user_id", item)  # 숫자 user_id 미노출

    def test_feed_exposes_pnl_when_opted_in(self):
        self.create_post(self.token1, show_pnl=True)
        item = self.client.get("/api/community/posts").json()["items"][0]
        self.assertEqual(item["journal"]["pnl_pct"], 5.0)

    # ---- 쓰기 게이트 ----
    def test_create_requires_auth(self):
        response = self.client.post("/api/community/posts", json={"title": "x"})
        self.assertEqual(response.status_code, 401)

    def test_create_rejects_other_users_journal(self):
        # user2 가 user1 소유 저널(20)을 공유 시도.
        response = self.create_post(self.token2, journal_id=20)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "NOT_JOURNAL_OWNER")

    def test_create_rejects_unknown_journal(self):
        response = self.create_post(self.token1, journal_id=999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "JOURNAL_NOT_FOUND")

    def test_create_with_own_journal_appears_in_feed(self):
        created = self.create_post(self.token1, journal_id=20)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["author"]["member_code"], "AAAA2345")
        feed = self.client.get("/api/community/posts").json()["items"]
        self.assertEqual(feed[0]["id"], created.json()["id"])

    # ---- 반응 ----
    def test_self_like_blocked(self):
        post_id = self.create_post(self.token1).json()["id"]
        response = self.client.post(f"/api/community/posts/{post_id}/reactions",
                                    json={"type": "like"}, headers=self.headers(self.token1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "SELF_REACTION")

    def test_like_toggles(self):
        post_id = self.create_post(self.token1).json()["id"]
        url = f"/api/community/posts/{post_id}/reactions"
        first = self.client.post(url, json={"type": "like"}, headers=self.headers(self.token2))
        self.assertEqual(first.json(), {"action": "added", "type": "like", "like_count": 1})
        second = self.client.post(url, json={"type": "like"}, headers=self.headers(self.token2))
        self.assertEqual(second.json()["action"], "removed")
        self.assertEqual(second.json()["like_count"], 0)

    # ---- 댓글 1단계 대댓글 ----
    def test_comment_reply_depth_enforced(self):
        post_id = self.create_post(self.token1).json()["id"]
        c1 = self.client.post(f"/api/community/posts/{post_id}/comments",
                              json={"body": "좋은 판단이네요"}, headers=self.headers(self.token2))
        self.assertEqual(c1.status_code, 200)
        reply = self.client.post(f"/api/community/posts/{post_id}/comments",
                                 json={"body": "감사합니다", "parent_comment_id": c1.json()["id"]},
                                 headers=self.headers(self.token1))
        self.assertEqual(reply.status_code, 200)
        # 대댓글에 또 대댓글 → 거부.
        nested = self.client.post(f"/api/community/posts/{post_id}/comments",
                                  json={"body": "안돼요", "parent_comment_id": reply.json()["id"]},
                                  headers=self.headers(self.token2))
        self.assertEqual(nested.status_code, 400)
        self.assertEqual(nested.json()["detail"]["code"], "COMMENT_TARGET_INVALID")

    # ---- 신고 자동숨김 ----
    def test_report_threshold_hides_and_removes_from_feed(self):
        post_id = self.create_post(self.token1).json()["id"]
        # 서로 다른 신고자 5명(임계) — 유저 3~7 추가.
        for uid in range(3, 8):
            self.connection.users_by_id[uid] = {"id": uid, "email": f"{uid}@e.com",
                                                "member_code": f"U{uid:03d}2345", "nickname": f"u{uid}",
                                                "agreed_risk": True, "is_verified": False}
            token = self.token_for(uid, f"{uid}@e.com")
            res = self.client.post(f"/api/community/posts/{post_id}/report",
                                   json={"reason": "spam"}, headers=self.headers(token))
            self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["hidden"])
        self.assertEqual(self.client.get("/api/community/posts").json()["items"], [])

    # ---- 404 / 권한 ----
    def test_detail_not_found(self):
        response = self.client.get("/api/community/posts/999")
        self.assertEqual(response.status_code, 404)

    def test_anonymous_detail_view_counts_once_per_session(self):
        post_id = self.create_post(self.token1).json()["id"]

        first = self.client.get(f"/api/community/posts/{post_id}")
        self.assertEqual(first.status_code, 200)
        self.assertIn("sa_community_viewer=", first.headers.get("set-cookie", ""))
        self.assertEqual(self.connection._find_post(post_id)["view_count"], 1)

        second = self.client.get(f"/api/community/posts/{post_id}")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.connection._find_post(post_id)["view_count"], 1)

    def test_delete_requires_owner(self):
        post_id = self.create_post(self.token1).json()["id"]
        response = self.client.delete(f"/api/community/posts/{post_id}", headers=self.headers(self.token2))
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
