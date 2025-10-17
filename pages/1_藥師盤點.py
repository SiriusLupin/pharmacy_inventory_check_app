# pages/1_藥師盤點.py
# ------------------------------------------------------------
# 藥師盤點頁（每台使用同一張分表，透過「儲位」切分區域）
# 功能：
# 1) 讀取 URL 參數 ?device=21 來決定要開啟哪一台的分表（例如：分表-21台）
# 2) 使用者輸入姓名（作為盤點人 owner），用於「僅原填寫者可修改」的控制
# 3) 依「儲位」的區域 (例如 B/C/D/E...) 篩選資料
# 4) 可搜尋藥品名稱（或代碼，若你之後加上該欄位也可）
# 5) 逐列編輯：盤點數量、備註；送出時寫回 Google Sheet
# 6) 僅原填寫者可修改（owner 不是自己 → disabled）
# 7) 可新增未列藥品（由盤點人新增）
# 8) 顯示簡單進度（已盤 vs 全筆數）
# ------------------------------------------------------------

import streamlit as st
import pandas as pd
from datetime import datetime
import re

# 這些工具函式來自你專案的資料層封裝（請確認 gs.py 有對應函式）
from gs import read_df, upsert_row, append_row, append_audit

# ----------------------------
# 基本頁面設定
# ----------------------------
st.set_page_config(page_title="藥師盤點", page_icon="💊", layout="wide")

# ----------------------------
# 讀取 URL 參數（預期 QR code 會帶 device）
# 例如：https://yourapp.streamlit.app/?device=21
# ----------------------------
params = st.experimental_get_query_params()
device = (params.get("device", ["21"])[0]).strip()

# 若 device 已含台或區字，就不重複加字尾
if any(device.endswith(suffix) for suffix in ["台", "區","超市"]):
    sheet_name = f"分表-{device}"
else:
    sheet_name = f"分表-{device}台"

st.caption(f"目前載入分表名稱：{sheet_name}")

# ----------------------------
# 頁面標題與使用者（盤點人）輸入
# ----------------------------
st.title(f"📋 {device} 台盤點作業")
st.caption("提示：掃描該台的 QR code 會自動帶入 device 參數")

# 盤點人：建議必填，因為之後會用這個做「原填寫者可修改」
user = st.text_input("請輸入你的姓名（將記錄為盤點人）", max_chars=20)
if not user:
    st.info("請先輸入姓名以開始盤點。")
    st.stop()

# ----------------------------
# 預設欄位名稱（請對應你的 Google Sheet 表頭）
# 你在問題中提到：藥品名稱, 藥品儲位, 盤點數量, 盤點人, 備註, 盤點時間
# 以下先用常見/通用欄名；若你的實際欄名不同，請同步調整此處
# ----------------------------
COL_NAME   = "藥品名稱"
COL_LOC    = "儲位"         # 儲位（用來判斷 A/B/C/D/E 區域）
COL_QTY    = "盤點數量"
COL_OWNER  = "盤點人"
COL_NOTE   = "備註"
COL_TIME   = "盤點時間"

# ----------------------------
# 讀取該台分表資料
# - read_df(sheet_name) 若表不存在，會回傳空 DataFrame（取決於你的 gs.py 實作）
# ----------------------------
df = read_df(sheet_name)

# 若首次建立或尚未有資料，提示並提供「新增未列藥品」功能
if df.empty:
    st.warning(f"目前「{sheet_name}」尚未有資料。你可以先新增一筆藥品，或請主管／Apps Script 產生基礎清單。")
    # 下方仍提供新增入口，讓你空表也可開始建資料
else:
    # 將空字串轉為缺值，避免之後判斷困難（保險）
    df = df.replace("", pd.NA)

# ----------------------------
# 推導區域列表（從「儲位」萃取區域字母，如 B, C, D, E ...）
# 規則：取儲位第一個英文字母；若取不到則標示為「未分類」
# ----------------------------
def extract_zone(loc: str) -> str:
    if not isinstance(loc, str):
        return "未分類"
    # 例如：B01, C12 → 取首字母
    m = re.match(r"([A-Za-z])", loc.strip())
    return m.group(1).upper() if m else "未分類"

# 當 df 為空時，zones 給一個預設以防崩潰
if df.empty or COL_LOC not in df.columns:
    zones = ["未分類"]
else:
    zones = sorted(df[COL_LOC].dropna().map(extract_zone).unique().tolist())
    if not zones:
        zones = ["未分類"]

# ----------------------------
# 介面：篩選區域、搜尋
# ----------------------------
col_filters = st.columns([1, 2, 2, 2])
with col_filters[0]:
    selected_zone = st.selectbox("選擇區域（依儲位字首）", zones, index=0)

with col_filters[1]:
    keyword = st.text_input("🔍 搜尋藥品名稱（關鍵字）", "")

with col_filters[2]:
    hide_completed = st.checkbox("隱藏已盤（有數量）", value=False,
                                 help="勾選後，只顯示尚未輸入盤點數量的藥品。")

with col_filters[3]:
    sort_by_loc = st.checkbox("依儲位排序", value=True)

# ----------------------------
# 依區域與搜尋條件篩選 df
# ----------------------------
df_view = df.copy()

# 1) 加入「區域」欄供顯示與篩選（視覺化用途）
if not df_view.empty:
    df_view["區域"] = df_view[COL_LOC].map(extract_zone)

# 2) 篩選區域
if selected_zone and selected_zone != "未分類":
    df_view = df_view[df_view["區域"] == selected_zone]
elif selected_zone == "未分類":
    df_view = df_view[df_view["區域"] == "未分類"]

# 3) 搜尋藥名（簡單包含）
if keyword:
    df_view = df_view[df_view[COL_NAME].fillna("").str.contains(keyword, case=False, na=False)]

# 4) 隱藏已盤（定義：盤點數量有值且不是空字串/NaN）
if hide_completed and not df_view.empty:
    df_view = df_view[df_view[COL_QTY].isna() | (df_view[COL_QTY].astype(str).str.strip() == "")]

# 5) 排序（預設依儲位）
if sort_by_loc and not df_view.empty and COL_LOC in df_view.columns:
    df_view = df_view.sort_values(by=[COL_LOC, COL_NAME], na_position="last")

# ----------------------------
# 進度統計（顯示在頁面上方）
# 定義完成：盤點數量欄位有值且非空
# ----------------------------
total_count = len(df) if not df.empty else 0
done_count = 0
if not df.empty and COL_QTY in df.columns:
    done_mask = ~(df[COL_QTY].isna() | (df[COL_QTY].astype(str).str.strip() == ""))
    done_count = int(done_mask.sum())

progress_col1, progress_col2, progress_col3 = st.columns(3)
with progress_col1:
    st.metric("總筆數", total_count)
with progress_col2:
    st.metric("已盤筆數", done_count)
with progress_col3:
    pct = 0 if total_count == 0 else round(done_count / total_count * 100, 1)
    st.metric("完成率", f"{pct}%")

st.progress(0 if total_count == 0 else done_count / max(1, total_count))

st.divider()

# ----------------------------
# 呈現資料與逐列編輯
# 說明：
# - 為了「僅原填寫者可修改」，這裡採「逐列輸入」而非一次性 data_editor
# - 若原 owner 為空或等於目前使用者，才可編輯
# - 每列有「儲存」按鈕；儲存後會 upsert 該筆資料
# - 寫入成功後清除快取（讓 read_df 立即看到最新資料）
# ----------------------------

# 若沒資料可顯示，提示後提供「新增未列藥品」入口
if df_view.empty:
    st.info("目前此篩選條件下沒有資料。你可以調整區域/搜尋條件，或在下方新增未列藥品。")
else:
    st.subheader(f"📄 顯示筆數：{len(df_view)}（篩選後）")

    # 逐列渲染：注意每個元件要有唯一 key，避免與其他列衝突
    for idx, row in df_view.iterrows():
        # 取 row 內容（用 get 避免 KeyError）
        name = str(row.get(COL_NAME, "") or "")
        loc  = str(row.get(COL_LOC, "") or "")
        qty  = row.get(COL_QTY, "")
        note = str(row.get(COL_NOTE, "") or "")
        owner_existing = str(row.get(COL_OWNER, "") or "")
        time_existing  = str(row.get(COL_TIME, "") or "")

        # 權限邏輯：owner 為空（尚未填）或就是這位使用者 → 可編輯；否則鎖定
        can_edit = (owner_existing.strip() == "") or (owner_existing.strip() == user.strip())

        # 每列用一個容器與 2 欄排版（左：基本資訊 + 輸入區；右：狀態與按鈕）
        with st.container():
            left, right = st.columns([3, 1])

            # --- 左側：主要欄位 ---
            with left:
                st.markdown(f"**{name}** 　·　儲位：`{loc}` 　·　區域：`{extract_zone(loc)}`")

                # 盤點數量（可編輯或鎖定）
                # 以「整數」處理；若你需要允許小數，可以改用 float 或 text_input
                init_qty = 0
                if isinstance(qty, (int, float)) and pd.notna(qty):
                    init_qty = int(qty)
                elif isinstance(qty, str) and qty.strip().isdigit():
                    init_qty = int(qty.strip())

                new_qty = st.number_input(
                    "盤點數量",
                    min_value=0,
                    value=init_qty,
                    step=1,
                    key=f"qty_{sheet_name}_{idx}",
                    disabled=not can_edit
                )

                new_note = st.text_input(
                    "備註",
                    value=note,
                    key=f"note_{sheet_name}_{idx}",
                    disabled=not can_edit
                )

            # --- 右側：資訊與提交 ---
            with right:
                st.caption(f"原填寫者：{owner_existing or '（尚未填）'}")
                st.caption(f"最後更新：{time_existing or '—'}")

                # 儲存按鈕（僅可編輯時可按）
                if st.button("💾 儲存", key=f"save_{sheet_name}_{idx}", disabled=not can_edit):
                    # upsert 的「鍵」建議用 (藥品名稱 + 儲位) 作為唯一定位
                    key_cols = {COL_NAME: name, COL_LOC: loc}

                    # old/new 值用於寫稽核（僅在數量變動時記錄）
                    old_val = str(qty or "")
                    new_val = str(new_qty)

                    # 準備寫回的 payload
                    payload = {
                        COL_NAME: name,
                        COL_LOC:  loc,
                        COL_QTY:  new_qty,
                        COL_NOTE: new_note,
                        COL_OWNER: user,  # 記錄/覆寫為當前使用者（誰最後改誰負責）
                        COL_TIME:  datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }

                    # 執行 upsert（覆寫或新增）
                    upsert_row(sheet_name, key_cols, payload)

                    # 如有變動，寫入稽核（可供主管追蹤何時誰改了什麼）
                    if old_val != new_val:
                        append_audit({
                            "ts": payload[COL_TIME],
                            "device": device,
                            "zone": extract_zone(loc),  # 方便之後按區域查
                            "drug_code": name,          # 你若有「藥碼」欄位，請用藥碼；此處暫以名稱代替
                            "field": COL_QTY,
                            "old_value": old_val,
                            "new_value": new_val,
                            "user": user
                        })

                    # 清快取：讓 read_df 重新抓最新資料（你在 gs.read_df 上用了 @st.cache_data）
                    st.cache_data.clear()
                    st.success("✅ 已儲存！")
                    # 立即刷新頁面資料（可選）
                    st.rerun()

        st.markdown("---")  # 每列分隔線，視覺較清楚

# ----------------------------
# 新增未列藥品（由盤點人新增）
# - 寫入同一張分表（該台）
# - 儲位必填，因為要靠它區分區域
# ----------------------------
with st.expander("➕ 新增未列藥品（此台）"):
    new_cols = st.columns([2, 1, 1, 3])
    with new_cols[0]:
        new_name = st.text_input("藥品名稱", key=f"new_name_{sheet_name}")
    with new_cols[1]:
        new_loc = st.text_input("儲位（如 B01）", key=f"new_loc_{sheet_name}")
    with new_cols[2]:
        new_qty_val = st.number_input("盤點數量", min_value=0, value=0, key=f"new_qty_{sheet_name}")
    with new_cols[3]:
        new_note_val = st.text_input("備註（可留空）", key=f"new_note_{sheet_name}")

    if st.button("新增此藥品", key=f"add_{sheet_name}"):
        if not new_name or not new_loc:
            st.error("請至少填寫「藥品名稱」與「儲位」。")
        else:
            payload = {
                COL_NAME: new_name.strip(),
                COL_LOC:  new_loc.strip(),
                COL_QTY:  int(new_qty_val),
                COL_NOTE: new_note_val.strip(),
                COL_OWNER: user,
                COL_TIME:  datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            append_row(sheet_name, payload)
            # 也可寫一筆稽核（標記「新增項目」）
            append_audit({
                "ts": payload[COL_TIME],
                "device": device,
                "zone": extract_zone(new_loc),
                "drug_code": new_name.strip(),
                "field": "新增項目",
                "old_value": "",
                "new_value": f"{COL_QTY}={payload[COL_QTY]}",
                "user": user
            })
            st.cache_data.clear()
            st.success("✅ 已新增！")
            st.rerun()

# ----------------------------
# 小提醒：
# - 若你需要「填寫後自動隱藏」功能，可善用「隱藏已盤」勾選或預設打勾
# - 若你有「藥碼」欄位，建議將 key_cols 改為 (藥碼 + 儲位) 更穩定
# - 若某些台不需要「區域」概念，儲位也可以填固定碼（例如 X01），前端一樣能顯示
# ----------------------------
