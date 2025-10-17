# gs.py
# --- 核心：與 Google Sheets 連線 + 常用存取工具 ---
# 你可以把「資料層」的讀寫全部集中在這支，之後前端頁面只呼叫函式即可。

from __future__ import annotations

# gspread：Python 操作 Google Sheet 的常用套件
import gspread
# Credentials：用來把 Service Account 的 JSON 轉成可用憑證
from google.oauth2.service_account import Credentials
# pandas：把 Sheet 內容轉成 DataFrame，對篩選/統計很方便
import pandas as pd
# streamlit：用來讀 secrets（我們把 SA 金鑰與 sheet_id 放在 secrets.toml）
import streamlit as st
# typing：型別註解（非必須，但讓 IDE 智能較好）
from typing import List, Dict, Any, Sequence


# ===== 連線區 =====

# Google Sheets API 權限範圍：讀寫試算表
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 從 Streamlit 的 secrets 載入 Service Account JSON，建立憑證
CREDS = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=SCOPES
)

# 用憑證授權 gspread，取得 client
_gc = gspread.authorize(CREDS)

# 透過試算表 ID 開啟活頁簿（ID 來自 secrets）
_SH = _gc.open_by_key(st.secrets["app"]["sheet_id"])


# ===== 常用工具 =====

def ensure_worksheet(name: str, headers: Sequence[str] | None = None):
    """
    取得指定名稱的工作表；若不存在就建立一張。
    如果有傳入 headers，且工作表原本是空的，則在第 1 列寫入表頭。
    """
    try:
        ws = _SH.worksheet(name)  # 嘗試拿到既有工作表
    except gspread.WorksheetNotFound:
        ws = _SH.add_worksheet(title=name, rows=1000, cols=50)  # 不存在就新建
        if headers:
            ws.append_row(list(headers))  # 寫入表頭
    else:
        # 若有要求 headers，但表內目前沒資料，也補上表頭
        if headers:
            vals = ws.get_all_values()
            if len(vals) == 0:
                ws.append_row(list(headers))
    return ws


@st.cache_data(ttl=5)  # 簡單快取 5 秒，減少讀表次數（可依需要調整或移除）
def read_df(sheet_name: str) -> pd.DataFrame:
    """
    把工作表整張讀成 DataFrame。
    - 若工作表不存在，回傳空的 DataFrame。
    - 若只有表頭沒有資料，回傳空表頭 DataFrame。
    """
    try:
        ws = _SH.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()  # 不存在就回空表

    rows = ws.get_all_values()  # 取得所有儲存格文字（包含表頭）
    if not rows:
        return pd.DataFrame()

    headers = rows[0]          # 第一列當表頭
    records = rows[1:]         # 其餘列當資料
    df = pd.DataFrame(records, columns=headers)

    # 把空字串視為缺值（方便後續判斷）
    df = df.replace("", pd.NA)
    return df


def _normalize_headers(ws, record: Dict[str, Any]) -> List[str]:
    """
    確保工作表的表頭能涵蓋 record 的所有欄位。
    - 若缺欄位，會把新欄位補在表尾（第一列）。
    - 回傳更新後的完整欄位順序（list）。
    """
    values = ws.get_all_values()
    if values:
        headers = values[0]
    else:
        headers = []

    # 找出 record 中沒在表頭的欄位
    missing = [k for k in record.keys() if k not in headers]
    if missing:
        # 把新欄位補上去
        new_headers = headers + missing
        ws.delete_rows(1)                 # 先刪掉舊表頭（第一列）
        ws.insert_row(new_headers, 1)     # 插入新的表頭
        return new_headers
    return headers


def append_row(sheet_name: str, record: Dict[str, Any]):
    """
    在指定工作表最後面新增一列。
    - 若工作表不存在會先建立。
    - 若 record 有新欄位，會自動補表頭。
    """
    ws = ensure_worksheet(sheet_name)
    headers = _normalize_headers(ws, record)
    # 依表頭順序產生一列值，找不到的欄位填空字串
    row = [record.get(h, "") for h in headers]
    ws.append_row(row)


def upsert_row(sheet_name: str, key_cols: Sequence[str], record: Dict[str, Any]):
    """
    依 key_cols（組合鍵）找到目標列：
    - 若找到 → 覆寫整列（用 record 提供的欄位；沒提供的留空或原值，視你的策略而定）
    - 若找不到 → 追加一列
    注意：
    - 這裡為了簡潔，採「覆寫整列」策略：未出現在 record 的欄位以空字串覆寫。
      如果你想保留原值，可先讀 df 合併 record 再寫回。
    """
    ws = ensure_worksheet(sheet_name)
    headers = _normalize_headers(ws, record)

    # 把整張表讀成 DataFrame 方便比對（小型表 ok；若資料很大建議做分頁/索引）
    df = read_df(sheet_name)

    # 如果表還是空的（只有表頭），直接 append
    if df.empty:
        row = [record.get(h, "") for h in headers]
        ws.append_row(row)
        return

    # 逐一套用 key 篩選條件（全部相等才視為找到該列）
    mask = pd.Series([True] * len(df))
    for k in key_cols:
        mask &= (df[k].astype(str).fillna("") == str(record.get(k, "")))

    if mask.any():
        # 取第一個符合的 row index
        idx = mask[mask].index[0]
        # gspread 的列/行是 1-base，且第一列是表頭，所以資料列要 +2
        row_number = idx + 2
        # 依目前 headers 覆寫整列內容（簡化做法）
        values = [[record.get(h, "") for h in headers]]
        # 設定要覆寫的範圍：從第 1 欄到 headers 長度
        ws.update(f"A{row_number}:{_col_letter(len(headers))}{row_number}", values)
    else:
        # 找不到 → 直接 append
        row = [record.get(h, "") for h in headers]
        ws.append_row(row)


def append_audit(log: Dict[str, Any]):
    """
    寫入稽核紀錄到 `Audit_Log` 工作表。
    預設欄位順序：['ts','device','zone','drug_code','field','old_value','new_value','user']
    """
    headers = ['ts','device','zone','drug_code','field','old_value','new_value','user']
    ws = ensure_worksheet("Audit_Log", headers=headers)
    # 依欄位順序取值，不存在填空字串
    row = [log.get(h, "") for h in headers]
    ws.append_row(row)


# ===== 小工具：把欄號轉成 A1 表達法的字母（1->A, 2->B, ... 27->AA） =====
def _col_letter(n: int) -> str:
    """
    將數字欄位索引轉換成 Excel/Sheets 的欄字母（1 -> 'A', 27 -> 'AA'）。
    """
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(r + 65) + s
    return s
