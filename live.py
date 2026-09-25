from io import BytesIO
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import ipaddress
import logging
import os
import time

import httpx
import pymysql
import pymysql.cursors
import qrcode

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from promptpay import qrcode as promptpay_qrcode


# =========================================================
# CONFIGURATION
# =========================================================

APP_TITLE = "Nexgen Payment Gateway API"
APP_VERSION = "1.8.0"

ROOT_PATH = ""

TRUEMONEY_API_URL = os.getenv(
    "TRUEMONEY_API_URL",
    "https://tmn.zelthr.rest/redeem",
)

SLIP_VERIFY_API_URL = os.getenv(
    "SLIP_VERIFY_API_URL",
    "https://slips.zelthr.rest/verify",
)

NEXGEN_MANAGEMENT_KEY = os.getenv("NEXGEN_MANAGEMENT_KEY", "nexgenapidev")

# ---------------------------------------------------------
# MySQL
# ---------------------------------------------------------

MYSQL_HOST = "15.235.227.117"
MYSQL_PORT = 3306
MYSQL_USER = "qtl0nexgenap_api"
MYSQL_PASSWORD = "vBnDUn2E9gJC4YEe7Y8v"
MYSQL_DATABASE = "qtl0nexgenap_api"

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "[%(levelname)s] "
        "[nexgenapi] "
        "%(message)s"
    ),
)

logger = logging.getLogger("nexgenapi")


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title=APP_TITLE,
    description="""
Nexgen Payment Gateway API

Payment Gateway API สำหรับระบบชำระเงินของ Nexgen
""",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    root_path=ROOT_PATH,
)


# =========================================================
# STANDARD API ERROR RESPONSES
# =========================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        payload = dict(detail)
        nested_status = payload.get("status")
        if isinstance(nested_status, dict):
            code = nested_status.get("code")
            nested_message = nested_status.get("message")
            payload["status"] = exc.status_code
            if code and "error" not in payload:
                payload["error"] = code
            if nested_message and "message" not in payload:
                payload["message"] = nested_message

        payload["status"] = exc.status_code
        payload.setdefault("message", "Request failed")
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=exc.headers,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": exc.status_code,
            "error": "HTTP_ERROR",
            "message": str(detail),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "status": 422,
            "error": "VALIDATION_ERROR",
            "message": "The request contains invalid parameters",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled API exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "status": 500,
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An internal server error occurred",
        },
    )


# =========================================================
# MODELS
# =========================================================

class PaymentRequest(BaseModel):
    promptpay: str
    amount: float | None = None


class TrueMoneyRedeemRequest(BaseModel):
    gift: str
    phone: str


class SlipVerifyRequest(BaseModel):
    qrcode: str
    amount: float


class IPAccessRequest(BaseModel):
    ip: str
    access_type: str = "ALLOW"
    description: str | None = None
    expires_at: datetime | None = None
    token_id: int | None = None


# =========================================================
# MySQL CONNECTION
# =========================================================

@contextmanager
def get_db():
    if not MYSQL_HOST:
        raise RuntimeError("MYSQL_HOST is not configured")

    connection = None
    try:
        connection = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
        )
        yield connection
    finally:
        if connection:
            connection.close()


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def initialize_database():
    try:
        with get_db() as conn:
            with conn.cursor() as cursor:
                # -------------------------------------------------
                # API TOKENS
                # -------------------------------------------------
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS API_Tokens (
                        ID BIGINT AUTO_INCREMENT PRIMARY KEY,
                        UserID BIGINT NOT NULL,
                        TokenHash CHAR(64) NOT NULL UNIQUE,
                        TokenPrefix VARCHAR(32) NULL,
                        Apikey VARCHAR(255) NULL,
                        Status TINYINT(1) NOT NULL DEFAULT 1,
                        ExpiredAt DATETIME NULL,
                        CreatedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        LastUsedAt DATETIME NULL,
                        Description VARCHAR(255) NULL,
                        INDEX IX_API_Tokens_UserID (UserID),
                        INDEX IX_API_Tokens_ExpiredAt (ExpiredAt),
                        INDEX IX_API_Tokens_Status (Status)
                    );
                """)

                # -------------------------------------------------
                # REQUEST LOG
                # -------------------------------------------------
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS API_RequestLogs (
                        ID BIGINT AUTO_INCREMENT PRIMARY KEY,
                        UserID BIGINT NULL,
                        ClientIP VARCHAR(64) NULL,
                        Country VARCHAR(16) NULL,
                        CFRay VARCHAR(128) NULL,
                        Method VARCHAR(16) NULL,
                        Path VARCHAR(500) NULL,
                        QueryString TEXT NULL,
                        StatusCode INT NULL,
                        ResponseTimeMs BIGINT NULL,
                        UserAgent VARCHAR(1000) NULL,
                        CreatedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX IX_API_RequestLogs_UserID (UserID),
                        INDEX IX_API_RequestLogs_CreatedAt (CreatedAt),
                        INDEX IX_API_RequestLogs_ClientIP (ClientIP)
                    );
                """)

                # -------------------------------------------------
                # IP ACCESS CONTROL
                # -------------------------------------------------
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS API_IP_Access (
                        ID BIGINT AUTO_INCREMENT PRIMARY KEY,
                        UserID BIGINT NULL,
                        TokenID BIGINT NULL,
                        IPAddress VARCHAR(64) NULL,
                        CIDR VARCHAR(64) NULL,
                        AccessType VARCHAR(10) NOT NULL DEFAULT 'ALLOW',
                        Status TINYINT(1) NOT NULL DEFAULT 0,
                        ApprovalStatus VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        Description VARCHAR(255) NULL,
                        CreatedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UpdatedAt DATETIME NULL,
                        ExpiresAt DATETIME NULL,
                        CreatedBy BIGINT NULL,
                        INDEX IX_API_IP_Access_UserID (UserID),
                        INDEX IX_API_IP_Access_TokenID (TokenID),
                        INDEX IX_API_IP_Access_IPAddress (IPAddress),
                        INDEX IX_API_IP_Access_Status (Status)
                    );
                """)

                # -------------------------------------------------
                # MANAGEMENT LOGS
                # -------------------------------------------------
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS API_ManagementLogs (
                        ID BIGINT AUTO_INCREMENT PRIMARY KEY,
                        AdminUserID BIGINT NULL,
                        TargetUserID BIGINT NULL,
                        Action VARCHAR(64) NOT NULL,
                        TokenID BIGINT NULL,
                        IPAddress VARCHAR(64) NULL,
                        Description VARCHAR(500) NULL,
                        CreatedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX IX_API_ManagementLogs_TargetUserID (TargetUserID),
                        INDEX IX_API_ManagementLogs_CreatedAt (CreatedAt)
                    );
                """)
            conn.commit()
        logger.info("MySQL database initialization completed")
    except Exception as exc:
        logger.exception("MySQL database initialization failed: %s", exc)


@app.get("/setup-db")
def setup_db_manual():
    try:
        # 1. บังคับเรียกคำสั่งสร้างตารางอีกครั้ง
        initialize_database()
        
        # 2. ดึงรายชื่อตารางทั้งหมดที่มีในระบบออกมาดู
        with get_db() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES;")
                tables = cursor.fetchall()
                
        return {
            "status": "success", 
            "message": "ระบบทำงานเสร็จสิ้น นี่คือรายชื่อตารางใน Database ของคุณ:", 
            "tables": tables
        }
    except Exception as exc:
        # ถ้าเชื่อมต่อไม่ได้ หรือสร้างตารางไม่ได้ จะแสดง Error บนหน้าเว็บเลย
        return {
            "status": "error", 
            "message": f"เกิดข้อผิดพลาด: {str(exc)}"
        }


# เรียกใช้ฟังก์ชันสร้างตารางทันทีที่ไฟล์ถูกโหลด (ต้องอยู่ระดับ module ไม่ใช่ใน function)
initialize_database()


# =========================================================
# TOKEN HASH
# =========================================================

def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# =========================================================
# CLOUDflare REAL IP
# =========================================================

def get_real_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def get_cf_ray(request: Request) -> str | None:
    return request.headers.get("CF-Ray")


def get_cf_country(request: Request) -> str | None:
    return request.headers.get("CF-IPCountry")


def normalize_ip_rule(value: str):
    value = value.strip()
    if not value:
        raise ValueError("IP address is required")
    try:
        if "/" in value:
            network = ipaddress.ip_network(value, strict=False)
            return None, str(network)
        address = ipaddress.ip_address(value)
        return str(address), None
    except ValueError as exc:
        raise ValueError("Invalid IP address or CIDR") from exc


def ip_rule_matches(client_ip: str, ip_address: str | None, cidr: str | None) -> bool:
    try:
        address = ipaddress.ip_address(client_ip)
        if ip_address:
            return address == ipaddress.ip_address(ip_address)
        if cidr:
            return address in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return False


def check_ip_access(request: Request, auth: dict):
    client_ip = get_real_client_ip(request)
    if client_ip == "unknown":
        raise HTTPException(403, detail={
            "status": 403,
            "error": "CLIENT_IP_UNAVAILABLE",
            "message": "Unable to determine client IP address",
        })

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ID, UserID, TokenID, IPAddress, CIDR, AccessType,
                   Status, ApprovalStatus, ExpiresAt
            FROM API_IP_Access
            WHERE (UserID IS NULL OR UserID = %s)
              AND (TokenID IS NULL OR TokenID = %s)
              AND (ExpiresAt IS NULL OR ExpiresAt > %s)
            ORDER BY
                CASE WHEN TokenID = %s THEN 1 ELSE 0 END DESC,
                CASE WHEN UserID = %s THEN 1 ELSE 0 END DESC,
                ID DESC
        """, (auth["user_id"], auth["token_id"], now, auth["token_id"], auth["user_id"]))
        rows = cursor.fetchall()
        cursor.close()

    rules = list(rows)
    active = [
        r for r in rules
        if bool(r["Status"])
        and str(r["ApprovalStatus"] or "PENDING").upper() == "APPROVED"
    ]

    pending = [
        r for r in rules
        if str(r["ApprovalStatus"] or "PENDING").upper() == "PENDING"
    ]

    if any(str(r["AccessType"]).upper() == "DENY" and ip_rule_matches(client_ip, r["IPAddress"], r["CIDR"]) for r in active):
        raise HTTPException(403, detail={
            "status": 403,
            "error": "IP_ACCESS_DENIED",
            "message": "Client IP is explicitly denied",
            "ip": client_ip,
        })

    allow_rules = [r for r in active if str(r["AccessType"]).upper() == "ALLOW"]
    if allow_rules:
        if not any(ip_rule_matches(client_ip, r["IPAddress"], r["CIDR"]) for r in allow_rules):
            raise HTTPException(403, detail={
                "status": 403,
                "error": "IP_NOT_WHITELISTED",
                "message": "Client IP is not in the approved IP list",
                "ip": client_ip,
            })
        return

    if pending:
        raise HTTPException(403, detail={
            "status": 403,
            "error": "IP_ACCESS_PENDING",
            "message": "IP access is pending administrator approval",
            "ip": client_ip,
        })

    if not rules:
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO API_IP_Access
                    (UserID, TokenID, IPAddress, CIDR, AccessType, Status, ApprovalStatus, Description, CreatedBy)
                    VALUES (%s, %s, %s, NULL, 'ALLOW', 0, 'PENDING', %s, %s)
                """, (
                    auth["user_id"],
                    auth["token_id"],
                    client_ip,
                    "Automatically created from first API request",
                    auth["user_id"],
                ))
                rule_id = cursor.lastrowid
                conn.commit()
                cursor.close()

            logger.info("Created pending IP access request: rule_id=%s user_id=%s token_id=%s ip=%s", rule_id, auth["user_id"], auth["token_id"], client_ip)
        except Exception as exc:
            logger.exception("Failed to auto-create IP access request: %s", exc)

        raise HTTPException(403, detail={
            "status": 403,
            "error": "IP_ACCESS_PENDING",
            "message": "IP access request created and is pending administrator approval",
            "ip": client_ip,
        })

    raise HTTPException(403, detail={
        "status": 403,
        "error": "IP_ACCESS_REQUIRED",
        "message": "No approved IP access rule is configured for this API token",
        "ip": client_ip,
    })


# =========================================================
# API AUTHENTICATION
# =========================================================

def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(
        default=None,
        description="Nexgen API Key เช่น NXG_live_xxxxxxxxx",
    ),
):
    if not x_api_key:
        raise HTTPException(401, detail={"status": 401, "error": "API_KEY_REQUIRED", "message": "API Key is required"})

    token_hash = hash_api_token(x_api_key)
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ID, UserID, Status, ExpiredAt, TokenPrefix
                FROM API_Tokens
                WHERE TokenHash = %s
            """, (token_hash,))
            row = cursor.fetchone()

            if not row:
                cursor.close()
                raise HTTPException(403, detail={"status": 403, "error": "INVALID_API_KEY", "message": "The provided API Key is invalid"})

            token_id = row["ID"]
            user_id = row["UserID"]
            status = row["Status"]
            expired_at = row["ExpiredAt"]
            token_prefix = row["TokenPrefix"]

            if not status:
                cursor.close()
                raise HTTPException(403, detail={"status": 403, "error": "API_KEY_DISABLED", "message": "This API Key has been disabled"})

            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if expired_at is not None and expired_at <= now_utc:
                cursor.close()
                raise HTTPException(403, detail={"status": 403, "error": "API_KEY_EXPIRED", "message": "This API Key has expired", "expired_at": expired_at.isoformat()})

            # -------------------------------------------------------------
            # NEW: Check User Plan from Web_Users Table
            # -------------------------------------------------------------
            try:
                cursor.execute("SELECT plan_expires_at FROM Web_Users WHERE id = %s", (user_id,))
                web_user = cursor.fetchone()
                if web_user and web_user.get("plan_expires_at"):
                    if web_user["plan_expires_at"] <= now_utc:
                        cursor.close()
                        raise HTTPException(403, detail={"status": 403, "error": "PLAN_EXPIRED", "message": "Your subscription plan has expired. Please top up on the website."})
            except Exception as e:
                pass # Ignore if Web_Users table doesn't exist yet (backward compatibility)
            # -------------------------------------------------------------

            cursor.execute("UPDATE API_Tokens SET LastUsedAt = UTC_TIMESTAMP() WHERE ID = %s", (token_id,))
            conn.commit()
            cursor.close()

        request.state.user_id = user_id
        request.state.token_id = token_id
        request.state.token_prefix = token_prefix

        try:
            check_ip_access(request, {"user_id": user_id, "token_id": token_id})
        except HTTPException:
            raise
        except pymysql.Error as exc:
            logger.exception("MySQL IP access check error: %s", exc)
            raise HTTPException(503, detail={"status": 503, "error": "IP_ACCESS_DATABASE_ERROR", "message": "Unable to check IP access policy"})

        return {
            "user_id": user_id,
            "token_id": token_id,
            "token_prefix": token_prefix,
            "expired_at": expired_at,
        }

    except HTTPException:
        raise
    except pymysql.Error as exc:
        logger.exception("MySQL authentication error: %s", exc)
        raise HTTPException(503, detail={"status": 503, "error": "AUTH_DATABASE_ERROR", "message": "Unable to connect to authentication database"})
    except Exception as exc:
        logger.exception("Authentication error: %s", exc)
        raise HTTPException(500, detail={"status": 500, "error": "AUTHENTICATION_ERROR", "message": "Authentication failed"})


# =========================================================
# REQUEST LOGGING
# =========================================================

def save_request_log(request: Request, status_code: int, response_time_ms: int):
    client_ip = get_real_client_ip(request)
    country = get_cf_country(request)
    cf_ray = get_cf_ray(request)
    user_id = getattr(request.state, "user_id", None)
    user_agent = request.headers.get("User-Agent")
    query_string = str(request.url.query)

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO API_RequestLogs (
                    UserID, ClientIP, Country, CFRay, Method, Path,
                    QueryString, StatusCode, ResponseTimeMs, UserAgent
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, client_ip, country, cf_ray, request.method, request.url.path, query_string, status_code, response_time_ms, user_agent))
            conn.commit()
            cursor.close()
    except Exception as exc:
        logger.exception("Unable to save request log: %s", exc)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        status_code = 500
        raise
    finally:
        elapsed = time.perf_counter() - started
        response_time_ms = int(elapsed * 1000)
        client_ip = get_real_client_ip(request)
        user_id = getattr(request.state, "user_id", None)
        cf_ray = get_cf_ray(request)

        logger.info(
            'IP=%s UserID=%s CF-Ray=%s "%s %s" Status=%s Time=%sms',
            client_ip, user_id if user_id else "-", cf_ray if cf_ray else "-",
            request.method, request.url.path, status_code, response_time_ms
        )
        save_request_log(request, status_code, response_time_ms)
    return response


# =========================================================
# QR CODE
# =========================================================

def create_promptpay_qr(promptpay: str, amount: float | None = None) -> BytesIO:
    payload = promptpay_qrcode.generate_payload(promptpay, amount or 0)
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# =========================================================
# endpoints... (ข้ามไปที่ส่วนของ Management เพราะส่วนอื่นเหมือนเดิม)
# =========================================================

@app.get(
    "/",
    tags=["System"],
    summary="ตรวจสอบสถานะ API",
)
def root():

    return {
        "status": "200",
        "message": APP_TITLE,
        "version": APP_VERSION,
    }


# =========================================================
# PAYMENT - GET
# =========================================================

@app.get(
    "/qrpayment",
    tags=["Payment"],
    summary="สร้าง PromptPay QR Code",
)
def generate_qr_get(

    promptpay: str = Query(
        ...,
        description="หมายเลข PromptPay",
    ),

    amount: float | None = Query(
        None,
        description="จำนวนเงิน",
    ),

    auth=Depends(verify_api_key),
):

    qr_image = create_promptpay_qr(
        promptpay,
        amount,
    )

    return StreamingResponse(
        qr_image,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                "inline; "
                "filename=promptpay.png"
            ),
        },
    )


# =========================================================
# PAYMENT - POST
# =========================================================

@app.post(
    "/qrpayment",
    tags=["Payment"],
    summary="สร้าง PromptPay QR Code ด้วย JSON",
)
def generate_qr_post(

    request: PaymentRequest,

    auth=Depends(verify_api_key),

):

    qr_image = create_promptpay_qr(
        request.promptpay,
        request.amount,
    )

    return StreamingResponse(
        qr_image,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                "inline; "
                "filename=promptpay.png"
            ),
        },
    )


# =========================================================
# REDEEM
# =========================================================

@app.get(
    "/redeem",
    tags=["Redeem"],
    summary="ตรวจสอบระบบ Redeem",
)
def redeem():

    return {
        "status": "200",
        "message": "Redeem is Used!",
    }


# =========================================================
# TRUEMONEY REDEEM
# =========================================================

@app.post(
    "/redeem/truemoney",
    tags=["Redeem"],
    summary="Redeem TrueMoney Voucher",
)
async def redeem_truemoney(

    request: TrueMoneyRedeemRequest,

    auth=Depends(verify_api_key),

):

    payload = {
        "gift": request.gift,
        "phone": request.phone,
    }

    try:

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:

            response = await client.post(
                TRUEMONEY_API_URL,
                json=payload,
                headers={
                    "Content-Type":
                        "application/json",
                },
            )

        try:

            result = response.json()

        except ValueError:

            result = {
                "status": {
                    "code":
                        "INVALID_GATEWAY_RESPONSE",
                    "message":
                        response.text,
                }
            }

        return result

    except httpx.TimeoutException:

        raise HTTPException(
            status_code=504,
            detail={
                "status": {
                    "code":
                        "GATEWAY_TIMEOUT",
                    "message":
                        "TrueMoney Gateway timeout",
                }
            },
        )

    except httpx.RequestError as exc:

        raise HTTPException(
            status_code=502,
            detail={
                "status": {
                    "code":
                        "GATEWAY_ERROR",
                    "message":
                        str(exc),
                }
            },
        )


# =========================================================
# SLIP VERIFY
# =========================================================

@app.post(
    "/slipverify",
    tags=["Slip Verify"],
    summary="ตรวจสอบสลิปการโอนเงิน",
)
async def verify_slip(

    request: SlipVerifyRequest,

    auth=Depends(verify_api_key),

):

    payload = {
        "qrcode": request.qrcode,
        "amount": request.amount,
    }

    try:

        async with httpx.AsyncClient(
            timeout=30.0
        ) as client:

            response = await client.post(
                SLIP_VERIFY_API_URL,
                json=payload,
                headers={
                    "Content-Type":
                        "application/json",
                },
            )

        try:

            result = response.json()

        except ValueError:

            result = {
                "status": "error",
                "message": response.text,
            }

        return result

    except httpx.TimeoutException:

        raise HTTPException(
            status_code=504,
            detail={
                "status": "error",
                "message":
                    "Slip Verification Gateway timeout",
            },
        )

    except httpx.RequestError as exc:

        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "message":
                    "Slip Verification Gateway error: "
                    f"{exc}",
            },
        )


# =========================================================
# MANAGEMENT API (Budibase)
# =========================================================

class TokenCreateRequest(BaseModel):
    description: str | None = None
    expired_at: datetime | None = None
    prefix: str = "live"


class TokenStatusRequest(BaseModel):
    status: bool


class ManagementIPRequest(BaseModel):
    ip: str
    access_type: str = "ALLOW"
    description: str | None = None
    expires_at: datetime | None = None
    token_id: int | None = None
    created_by: int | None = None


def require_management_key(
    x_management_key: str | None = Header(default=None, alias="X-Management-Key"),
):
    if not NEXGEN_MANAGEMENT_KEY:
        raise HTTPException(503, detail={"status":503,"error":"MANAGEMENT_KEY_NOT_CONFIGURED","message":"Management API is not configured"})
    if not x_management_key or not hmac.compare_digest(x_management_key, NEXGEN_MANAGEMENT_KEY):
        raise HTTPException(401, detail={"status":401,"error":"INVALID_MANAGEMENT_KEY","message":"Invalid management key"})
    return True


def generate_api_token(prefix: str = "live") -> tuple[str, str]:
    """Return (full_token, display_prefix). display_prefix is only the fixed part, e.g. 'NXG_live_'."""
    prefix = "".join(c for c in prefix.strip() if c.isalnum() or c in "-_")[:20] or "live"
    display_prefix = f"NXG_{prefix}_"
    return display_prefix + secrets.token_urlsafe(32), display_prefix


def management_log(action: str, target_user_id=None, token_id=None, request: Request | None = None, description=None):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO API_ManagementLogs
                (AdminUserID, TargetUserID, Action, TokenID, IPAddress, Description)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                None,
                target_user_id,
                action,
                token_id,
                get_real_client_ip(request) if request else None,
                description,
            ))
            conn.commit()
            cur.close()
    except Exception as exc:
        logger.exception("Management audit log failed: %s", exc)


@app.post("/v1/management/users/{user_id}/tokens", tags=["Management"], summary="Create API Key")
def management_create_token(user_id: int, payload: TokenCreateRequest, request: Request, _=Depends(require_management_key)):
    if payload.expired_at and payload.expired_at.tzinfo:
        payload.expired_at = payload.expired_at.astimezone(timezone.utc).replace(tzinfo=None)
    token, prefix = generate_api_token(payload.prefix)
    token_hash = hash_api_token(token)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO API_Tokens
            (UserID, TokenHash, TokenPrefix, Apikey, Status, ExpiredAt, Description)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
        """, (user_id, token_hash, prefix, token, payload.expired_at, payload.description))
        token_id = cur.lastrowid
        conn.commit()
        cur.close()
    management_log("TOKEN_CREATED", user_id, token_id, request, payload.description)
    return {"status":201,"message":"API key created","token":{"id":token_id,"api_key":token,"token_prefix":prefix,"user_id":user_id,"status":True,"expired_at":payload.expired_at.isoformat() if payload.expired_at else None,"description":payload.description},"ip_access":{"required":True,"status":"PENDING","message":"An approved IP access rule is required before this API key can call protected endpoints"}}


@app.get("/v1/management/users/{user_id}/tokens", tags=["Management"], summary="List API Keys")
def management_list_tokens(user_id: int, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor()
        cur.execute("""
            SELECT ID, UserID, TokenPrefix, Status, ExpiredAt, CreatedAt, LastUsedAt, Description
            FROM API_Tokens WHERE UserID=%s ORDER BY ID DESC
        """, (user_id,))
        rows=cur.fetchall(); cur.close()
    return {"status":200,"count":len(rows),"data":[{"id":r["ID"],"user_id":r["UserID"],"token_prefix":r["TokenPrefix"],"status":bool(r["Status"]),"expired_at":r["ExpiredAt"].isoformat() if r["ExpiredAt"] else None,"created_at":r["CreatedAt"].isoformat() if r["CreatedAt"] else None,"last_used_at":r["LastUsedAt"].isoformat() if r["LastUsedAt"] else None,"description":r["Description"]} for r in rows]}


@app.post("/v1/management/tokens/{token_id}/status", tags=["Management"], summary="Enable or disable API Key")
def management_token_status(token_id: int, payload: TokenStatusRequest, request: Request, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("UPDATE API_Tokens SET Status=%s WHERE ID=%s", (int(payload.status), token_id)); affected=cur.rowcount; conn.commit(); cur.close()
    if not affected: raise HTTPException(404, detail={"status":404,"error":"TOKEN_NOT_FOUND","message":"API token not found"})
    management_log("TOKEN_ENABLED" if payload.status else "TOKEN_DISABLED", token_id=token_id, request=request)
    return {"status":200,"message":"API key status updated","id":token_id,"enabled":payload.status}


@app.delete("/v1/management/tokens/{token_id}", tags=["Management"], summary="Revoke API Key")
def management_revoke_token(token_id: int, request: Request, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("UPDATE API_Tokens SET Status=0 WHERE ID=%s", (token_id,)); affected=cur.rowcount; conn.commit(); cur.close()
    if not affected: raise HTTPException(404, detail={"status":404,"error":"TOKEN_NOT_FOUND","message":"API token not found"})
    management_log("TOKEN_REVOKED", token_id=token_id, request=request)
    return {"status":200,"message":"API key revoked","id":token_id}


@app.get("/v1/management/users/{user_id}/activity", tags=["Management"], summary="User API Activity")
def management_user_activity(user_id: int, limit: int = Query(100, ge=1, le=500), _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("""
            SELECT ID, UserID, ClientIP, Country, CFRay, Method, Path, QueryString, StatusCode, ResponseTimeMs, UserAgent, CreatedAt
            FROM API_RequestLogs WHERE UserID=%s ORDER BY CreatedAt DESC LIMIT %s
        """, (user_id, limit)); rows=cur.fetchall(); cur.close()
    return {"status":200,"count":len(rows),"data":[{"id":r["ID"],"user_id":r["UserID"],"ip":r["ClientIP"],"country":r["Country"],"cf_ray":r["CFRay"],"method":r["Method"],"path":r["Path"],"query":r["QueryString"],"status_code":r["StatusCode"],"response_time_ms":r["ResponseTimeMs"],"user_agent":r["UserAgent"],"created_at":r["CreatedAt"].isoformat() if r["CreatedAt"] else None} for r in rows]}


@app.get("/v1/management/users/{user_id}/ips", tags=["Management"], summary="User IP Rules")
def management_list_ips(user_id: int, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("SELECT ID,UserID,TokenID,IPAddress,CIDR,AccessType,Status,ApprovalStatus,Description,CreatedAt,UpdatedAt,ExpiresAt,CreatedBy FROM API_IP_Access WHERE UserID=%s ORDER BY ID DESC", (user_id,)); rows=cur.fetchall(); cur.close()
    return {"status":200,"count":len(rows),"data":[{"id":r["ID"],"user_id":r["UserID"],"token_id":r["TokenID"],"ip":r["IPAddress"],"cidr":r["CIDR"],"access_type":r["AccessType"],"status":bool(r["Status"]),"approval_status":r["ApprovalStatus"],"description":r["Description"],"created_at":r["CreatedAt"].isoformat() if r["CreatedAt"] else None,"updated_at":r["UpdatedAt"].isoformat() if r["UpdatedAt"] else None,"expires_at":r["ExpiresAt"].isoformat() if r["ExpiresAt"] else None,"created_by":r["CreatedBy"]} for r in rows]}


@app.post("/v1/management/users/{user_id}/ips", tags=["Management"], summary="Create User IP Rule")
def management_create_ip(user_id: int, payload: ManagementIPRequest, request: Request, _=Depends(require_management_key)):
    access_type=payload.access_type.upper().strip()
    if access_type not in {"ALLOW","DENY"}: raise HTTPException(400, detail={"status":400,"error":"INVALID_ACCESS_TYPE","message":"access_type must be ALLOW or DENY"})
    try: ip_address,cidr=normalize_ip_rule(payload.ip)
    except ValueError as exc: raise HTTPException(400, detail={"status":400,"error":"INVALID_IP","message":str(exc)})
    expires=payload.expires_at.astimezone(timezone.utc).replace(tzinfo=None) if payload.expires_at and payload.expires_at.tzinfo else payload.expires_at
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("""
            INSERT INTO API_IP_Access
            (UserID,TokenID,IPAddress,CIDR,AccessType,Status,ApprovalStatus,Description,ExpiresAt,CreatedBy)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            user_id, payload.token_id, ip_address, cidr, access_type,
            1 if access_type == "DENY" else 0,
            "APPROVED" if access_type == "DENY" else "PENDING",
            payload.description, expires, payload.created_by))
        rule_id=cur.lastrowid; conn.commit(); cur.close()
    management_log("IP_RULE_CREATED", user_id, None, request, f"{access_type} {payload.ip}")
    return {"status":201,"message":"IP access rule created","id":rule_id,"user_id":user_id,"ip":payload.ip,"access_type":access_type}


# ---------------------------------------------------------
# Flat versions (no {user_id} in the path) for Budibase / web forms
#   GET  /v1/management/users/ips?user_id=1&approval_status=PENDING
#   POST /v1/management/users/ips   body: {"user_id":1,"ip":"1.2.3.4",...}
# ---------------------------------------------------------

class ManagementIPFlatRequest(ManagementIPRequest):
    user_id: int


@app.get("/v1/management/users/ips", tags=["Management"], summary="List IP Rules (all users or filter by user_id)")
def management_list_ips_flat(
    user_id: int | None = Query(None, description="กรองตาม user (ไม่ใส่ = ทุก user)"),
    approval_status: str | None = Query(None, description="PENDING / APPROVED / REJECTED / DISABLED"),
    limit: int = Query(200, ge=1, le=1000),
    _=Depends(require_management_key),
):
    where, args = [], []
    if user_id is not None:
        where.append("UserID = %s"); args.append(user_id)
    if approval_status:
        where.append("ApprovalStatus = %s"); args.append(approval_status.strip().upper())
    sql = (
        "SELECT ID,UserID,TokenID,IPAddress,CIDR,AccessType,Status,ApprovalStatus,Description,"
        "CreatedAt,UpdatedAt,ExpiresAt,CreatedBy FROM API_IP_Access"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY ID DESC LIMIT %s"
    )
    args.append(limit)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(args))
        rows = cur.fetchall()
        cur.close()
    iso = lambda v: v.isoformat() if v else None
    return {"status": 200, "count": len(rows), "data": [
        {"id": r["ID"], "user_id": r["UserID"], "token_id": r["TokenID"], "ip": r["IPAddress"], "cidr": r["CIDR"],
         "access_type": r["AccessType"], "status": bool(r["Status"]), "approval_status": r["ApprovalStatus"],
         "description": r["Description"], "created_at": iso(r["CreatedAt"]), "updated_at": iso(r["UpdatedAt"]),
         "expires_at": iso(r["ExpiresAt"]), "created_by": r["CreatedBy"]}
        for r in rows
    ]}


@app.post("/v1/management/users/ips", tags=["Management"], summary="Create IP Rule (user_id in body)")
def management_create_ip_flat(payload: ManagementIPFlatRequest, request: Request, _=Depends(require_management_key)):
    return management_create_ip(payload.user_id, payload, request, True)


@app.post("/v1/management/ip/{rule_id}/allow", tags=["Management"], summary="Allow IP Rule")
def management_allow_ip(rule_id: int, request: Request, _=Depends(require_management_key)):
    return management_set_ip(rule_id,"ALLOW",request)


@app.post("/v1/management/ip/{rule_id}/deny", tags=["Management"], summary="Deny IP Rule")
def management_deny_ip(rule_id: int, request: Request, _=Depends(require_management_key)):
    return management_set_ip(rule_id,"DENY",request)


def management_set_ip(rule_id:int, access_type:str, request:Request):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("UPDATE API_IP_Access SET AccessType=%s,Status=1,ApprovalStatus='APPROVED',UpdatedAt=UTC_TIMESTAMP() WHERE ID=%s",(access_type,rule_id)); affected=cur.rowcount; conn.commit(); cur.close()
    if not affected: raise HTTPException(404, detail={"status":404,"error":"IP_RULE_NOT_FOUND","message":"IP access rule not found"})
    management_log("IP_RULE_"+access_type, request=request); return {"status":200,"message":f"IP rule set to {access_type}","id":rule_id,"access_type":access_type}


@app.post("/v1/management/ip/{rule_id}/reject", tags=["Management"], summary="Reject pending IP Rule")
def management_reject_ip(rule_id: int, request: Request, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor()
        cur.execute("UPDATE API_IP_Access SET Status=0,ApprovalStatus='REJECTED',UpdatedAt=UTC_TIMESTAMP() WHERE ID=%s", (rule_id,))
        affected=cur.rowcount
        conn.commit()
        cur.close()
    if not affected:
        raise HTTPException(404, detail={"status":404,"error":"IP_RULE_NOT_FOUND","message":"IP access rule not found"})
    management_log("IP_RULE_REJECTED", request=request)
    return {"status":200,"message":"IP access rule rejected","id":rule_id,"approval_status":"REJECTED"}


@app.delete("/v1/management/ip/{rule_id}", tags=["Management"], summary="Disable IP Rule")
def management_disable_ip(rule_id:int, request:Request, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("UPDATE API_IP_Access SET Status=0,ApprovalStatus='DISABLED',UpdatedAt=UTC_TIMESTAMP() WHERE ID=%s",(rule_id,)); affected=cur.rowcount; conn.commit(); cur.close()
    if not affected: raise HTTPException(404, detail={"status":404,"error":"IP_RULE_NOT_FOUND","message":"IP access rule not found"})
    management_log("IP_RULE_DISABLED", request=request); return {"status":200,"message":"IP access rule disabled","id":rule_id}


@app.get("/v1/management/users/{user_id}/overview", tags=["Management"], summary="User API Overview")
def management_overview(user_id:int, _=Depends(require_management_key)):
    with get_db() as conn:
        cur=conn.cursor(); cur.execute("""
            SELECT
              (SELECT COUNT(*) FROM API_Tokens WHERE UserID=%s) AS TokenCount,
              (SELECT COUNT(*) FROM API_Tokens WHERE UserID=%s AND Status=1 AND (ExpiredAt IS NULL OR ExpiredAt>UTC_TIMESTAMP())) AS ActiveTokenCount,
              (SELECT COUNT(*) FROM API_RequestLogs WHERE UserID=%s) AS RequestCount,
              (SELECT COUNT(*) FROM API_RequestLogs WHERE UserID=%s AND StatusCode>=400) AS ErrorCount,
              (SELECT COUNT(*) FROM API_IP_Access WHERE UserID=%s AND Status=1) AS ActiveIPRuleCount
        """,(user_id,user_id,user_id,user_id,user_id)); r=cur.fetchone(); cur.close()
    return {"status":200,"user_id":user_id,"tokens":r["TokenCount"],"active_tokens":r["ActiveTokenCount"],"requests":r["RequestCount"],"errors":r["ErrorCount"],"active_ip_rules":r["ActiveIPRuleCount"]}


# =========================================================
# API ACTIVITY / IP ACCESS CONTROL
# =========================================================

@app.get(
    "/v1/api/activity",
    tags=["API Management"],
    summary="ดูประวัติการเรียก API",
)
async def api_activity(
    auth=Depends(verify_api_key),
    limit: int = Query(100, ge=1, le=500),
):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                ID, UserID, ClientIP, Country, CFRay, Method, Path,
                QueryString, StatusCode, ResponseTimeMs, UserAgent, CreatedAt
            FROM API_RequestLogs
            WHERE UserID = %s
            ORDER BY CreatedAt DESC
            LIMIT %s
            """,
            (auth["user_id"], limit),
        )
        rows = cursor.fetchall()
        cursor.close()

    return {
        "status": 200,
        "count": len(rows),
        "data": [
            {
                "id": r["ID"],
                "user_id": r["UserID"],
                "ip": r["ClientIP"],
                "country": r["Country"],
                "cf_ray": r["CFRay"],
                "method": r["Method"],
                "path": r["Path"],
                "query": r["QueryString"],
                "status_code": r["StatusCode"],
                "response_time_ms": r["ResponseTimeMs"],
                "user_agent": r["UserAgent"],
                "created_at": r["CreatedAt"].isoformat() if r["CreatedAt"] else None,
            }
            for r in rows
        ],
    }


@app.get(
    "/v1/api/access/ip",
    tags=["API Management"],
    summary="รายการ IP ที่อนุญาตและไม่อนุญาต",
)
async def list_ip_access(auth=Depends(verify_api_key)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ID, UserID, TokenID, IPAddress, CIDR, AccessType, Status, ApprovalStatus,
                   Description, CreatedAt, UpdatedAt, ExpiresAt, CreatedBy
            FROM API_IP_Access
            WHERE UserID = %s AND (TokenID IS NULL OR TokenID = %s)
            ORDER BY ID DESC
            """,
            (auth["user_id"], auth["token_id"]),
        )
        rows = cursor.fetchall()
        cursor.close()

    return {
        "status": 200,
        "count": len(rows),
        "data": [
            {
                "id": r["ID"],
                "user_id": r["UserID"],
                "token_id": r["TokenID"],
                "ip": r["IPAddress"],
                "cidr": r["CIDR"],
                "access_type": r["AccessType"],
                "status": bool(r["Status"]),
                "approval_status": r["ApprovalStatus"],
                "description": r["Description"],
                "created_at": r["CreatedAt"].isoformat() if r["CreatedAt"] else None,
                "updated_at": r["UpdatedAt"].isoformat() if r["UpdatedAt"] else None,
                "expires_at": r["ExpiresAt"].isoformat() if r["ExpiresAt"] else None,
            }
            for r in rows
        ],
    }


@app.post(
    "/v1/api/access/ip",
    tags=["API Management"],
    summary="เพิ่มกฎ Allow/Deny IP",
)
async def create_ip_access(payload: IPAccessRequest, auth=Depends(verify_api_key)):
    access_type = payload.access_type.upper().strip()
    if access_type not in {"ALLOW", "DENY"}:
        raise HTTPException(400, detail={"status": 400, "error": "INVALID_ACCESS_TYPE", "message": "access_type must be ALLOW or DENY"})

    try:
        ip_address, cidr = normalize_ip_rule(payload.ip)
    except ValueError as exc:
        raise HTTPException(400, detail={"status": 400, "error": "INVALID_IP", "message": str(exc)})

    token_id = payload.token_id or auth["token_id"]
    if token_id != auth["token_id"]:
        raise HTTPException(403, detail={"status": 403, "error": "TOKEN_SCOPE_FORBIDDEN", "message": "You can only manage the current API token"})

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO API_IP_Access
            (UserID, TokenID, IPAddress, CIDR, AccessType, Status, ApprovalStatus, Description, ExpiresAt, CreatedBy)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                auth["user_id"], token_id, ip_address, cidr, access_type,
                1 if access_type == "DENY" else 0,
                "APPROVED" if access_type == "DENY" else "PENDING",
                payload.description,
                payload.expires_at.replace(tzinfo=None) if payload.expires_at else None,
                auth["user_id"],
            ),
        )
        rule_id = cursor.lastrowid
        conn.commit()
        cursor.close()

    return {"status": 201, "message": "IP access rule created", "id": rule_id, "ip": payload.ip, "access_type": access_type}


@app.post(
    "/v1/api/access/ip/{rule_id}/allow",
    tags=["API Management"],
    summary="อนุญาต IP",
)
async def allow_ip(rule_id: int, auth=Depends(verify_api_key)):
    return await set_ip_rule_status(rule_id, "ALLOW", auth)


@app.post(
    "/v1/api/access/ip/{rule_id}/deny",
    tags=["API Management"],
    summary="ไม่อนุญาต IP",
)
async def deny_ip(rule_id: int, auth=Depends(verify_api_key)):
    return await set_ip_rule_status(rule_id, "DENY", auth)


async def set_ip_rule_status(rule_id: int, access_type: str, auth: dict):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE API_IP_Access
            SET AccessType = %s,
                Status = CASE WHEN %s = 'DENY' THEN 1 ELSE 0 END,
                ApprovalStatus = CASE WHEN %s = 'DENY' THEN 'APPROVED' ELSE 'PENDING' END,
                UpdatedAt = UTC_TIMESTAMP()
            WHERE ID = %s AND UserID = %s AND (TokenID IS NULL OR TokenID = %s)
            """,
            (access_type, access_type, access_type, rule_id, auth["user_id"], auth["token_id"]),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()

    if not affected:
        raise HTTPException(404, detail={"status": 404, "error": "IP_RULE_NOT_FOUND", "message": "IP access rule not found"})
    return {"status": 200, "message": f"IP rule set to {access_type}", "id": rule_id, "access_type": access_type}


@app.delete(
    "/v1/api/access/ip/{rule_id}",
    tags=["API Management"],
    summary="ปิดใช้งานกฎ IP",
)
async def delete_ip_access(rule_id: int, auth=Depends(verify_api_key)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE API_IP_Access
            SET Status = 0, ApprovalStatus='DISABLED', UpdatedAt = UTC_TIMESTAMP()
            WHERE ID = %s AND UserID = %s AND (TokenID IS NULL OR TokenID = %s)
            """,
            (rule_id, auth["user_id"], auth["token_id"]),
        )
        affected = cursor.rowcount
        conn.commit()
        cursor.close()

    if not affected:
        raise HTTPException(404, detail={"status": 404, "error": "IP_RULE_NOT_FOUND", "message": "IP access rule not found"})
    return {"status": 200, "message": "IP access rule disabled", "id": rule_id}


# =========================================================
# DEBUG
# =========================================================

@app.get(
    "/debug",
    tags=["Debug"],
    summary="ตรวจสอบ Query Parameters",
)
async def debug(
    request: Request,
):

    return {

        "url": str(request.url),

        "root_path":
            request.scope.get(
                "root_path"
            ),

        "client_ip":
            get_real_client_ip(request),

        "cf_connecting_ip":
            request.headers.get(
                "CF-Connecting-IP"
            ),

        "cf_ray":
            get_cf_ray(request),

        "cf_country":
            get_cf_country(request),

        "user_id":
            getattr(
                request.state,
                "user_id",
                None,
            ),

        "query_params":
            dict(request.query_params),
    }