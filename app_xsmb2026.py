"""
app_xsmb.py — XSMB: lấy lịch sử trực tiếp từ minhngoc.net.vn (không dùng CSV),
kiểm định tính ngẫu nhiên + backtest phương pháp.

Chạy:  streamlit run app_xsmb.py
requirements: streamlit, requests, beautifulsoup4

Ý tưởng lấy dữ liệu: trang minhngoc hiển thị 7 kỳ/1 trang, nên chỉ cần tải
mỗi 7 ngày một trang là gom được lịch sử dài, nhẹ cho server nguồn.
Tất cả giữ trong bộ nhớ (st.session_state) — không ghi file.
"""
import re, math, random, datetime
from html import unescape
from collections import Counter
from typing import List, Dict, Tuple

import requests
import streamlit as st

N_LO = 27
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# (nhãn giải, số chữ số mỗi giải, số lượng giải) — cố định theo luật XSMB.
# Không phụ thuộc tên class HTML nên bền khi minhngoc đổi giao diện.
SPECS = [("nhất", 5, 1), ("nhì", 5, 2), ("ba", 5, 6), ("tư", 4, 4),
         ("năm", 4, 6), ("sáu", 3, 3), ("bảy", 2, 4)]


# ---------------- Parser ----------------
def _collect(s: str, n: int) -> str:
    """Lấy n chữ số đầu tiên trong chuỗi s (bỏ qua khoảng trắng/ký tự khác)."""
    out = []
    for ch in s:
        if ch.isdigit():
            out.append(ch)
            if len(out) == n:
                break
    return "".join(out)


def parse_page(html: str) -> Dict[str, List[int]]:
    """Trả về {iso_date: [27 số lô]} cho MỌI bảng ngày có trên trang.

    Cách làm: xoá thẻ HTML -> bám nhãn 'Giải ...' -> lấy đúng số chữ số cố định
    của từng giải -> lấy 2 số cuối. Bền với thay đổi class/div của minhngoc.
    """
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    out: Dict[str, List[int]] = {}
    starts = [m.start() for m in re.finditer(r"Giải\s+(?:ĐB|Đặc\s+Biệt)", text)]
    starts.append(len(text))
    for i in range(len(starts) - 1):
        seg = text[starts[i]:starts[i + 1]]
        pre = text[max(0, starts[i] - 400):starts[i]]
        dm = re.findall(r"(\d{2})/(\d{2})/(\d{4})", pre)   # ngày dạng dd/mm/yyyy
        if not dm:
            continue
        dd, mm, yy = dm[-1]
        iso = f"{yy}-{mm}-{dd}"
        # Giải ĐB: 5 chữ số ngay sau nhãn
        after = re.sub(r"^\s*Giải\s+(?:ĐB|Đặc\s+Biệt)", "", seg, count=1)
        db = _collect(after, 5)
        if len(db) < 5:
            continue
        lo = [int(db[-2:])]
        ok = True
        for label, width, count in SPECS:
            lm = re.search(r"Giải\s+" + label, seg)
            if not lm:
                ok = False; break
            digs = _collect(seg[lm.end():], width * count)
            if len(digs) < width * count:
                ok = False; break
            for j in range(count):
                lo.append(int(digs[j * width:(j + 1) * width][-2:]))
        if ok and len(lo) == N_LO:
            out[iso] = lo
    return out


def fetch_page(date: datetime.date) -> str:
    url = f"https://www.minhngoc.net.vn/ket-qua-xo-so/mien-bac/{date:%d-%m-%Y}.html"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def fetch_history(end: datetime.date, n_days: int,
                  progress=None) -> Tuple[Dict[str, List[int]], int]:
    """Gom ~n_days kỳ, tải mỗi 7 ngày 1 trang (mỗi trang cho 7 kỳ)."""
    hist: Dict[str, List[int]] = {}
    pages = math.ceil(n_days / 7) + 1
    fails = 0
    for i in range(pages):
        d = end - datetime.timedelta(days=i * 7)
        try:
            hist.update(parse_page(fetch_page(d)))
        except Exception:
            fails += 1
        if progress:
            progress((i + 1) / pages)
        if len(hist) >= n_days:
            break
    # cắt còn n_days kỳ gần nhất, sắp xếp tăng dần theo ngày
    items = sorted(hist.items())[-n_days:]
    return dict(items), fails


# ---------------- Thống kê ----------------
def base_rate() -> float:
    return 1 - (99 / 100) ** N_LO


def chi_square(days: List[List[int]]) -> Tuple[float, float, float]:
    c = Counter(n for d in days for n in d)
    total = sum(c.values()); exp = total / 100
    chi2 = sum((c.get(n, 0) - exp) ** 2 / exp for n in range(100))
    return chi2, exp, 123.2          # ngưỡng 5%, df=99


def wilson_ci(hits: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = hits / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def s_hot(days, k, window=30):
    c = Counter(n for d in days[-window:] for n in d)
    return [n for n, _ in c.most_common(k)]

def s_gan(days, k):
    last = {}
    for i, d in enumerate(days):
        for n in set(d):
            last[n] = i
    return sorted(range(100), key=lambda n: last.get(n, -1))[:k]

def s_random(days, k):
    return random.sample(range(100), k)

STRATS = {"nóng (hot)": s_hot, "gan": s_gan, "ngẫu nhiên": s_random}


def backtest(days: List[List[int]], k: int, warmup: int = 60):
    res = {name: 0 for name in STRATS}; trials = 0
    for t in range(warmup, len(days) - 1):
        hist = days[:t]; nxt = set(days[t + 1])
        for name, fn in STRATS.items():
            res[name] += sum(1 for p in fn(hist, k) if p in nxt)
        trials += 1
    return res, trials


# ---------------- Bảng lô hiện tại (tần suất/gan) để tham khảo ----------------
def current_table(days: List[List[int]]):
    cnt = Counter(n for d in days for n in d)
    last = {}
    for i, d in enumerate(days):
        for n in set(d):
            last[n] = i
    rows = []
    for n in range(100):
        gan = (len(days) - 1 - last[n]) if n in last else len(days)
        rows.append({"Số": f"{n:02d}", "Số lần về": cnt.get(n, 0), "Gan (ngày)": gan})
    return rows


# ---------------- UI ----------------
st.set_page_config(page_title="XSMB · minhngoc", page_icon="📊", layout="centered")
st.title("XSMB · Lịch sử từ minhngoc.net.vn")
st.caption("Lấy trực tiếp từ minhngoc, giữ trong bộ nhớ (không CSV). "
           "Công cụ để KIỂM CHỨNG phương pháp, không hứa 'số đẹp'.")

c1, c2 = st.columns(2)
with c1:
    end = st.date_input("Đến ngày", value=datetime.date.today())
with c2:
    n_days = st.slider("Số kỳ lịch sử cần lấy", 30, 400, 120, step=10)

if st.button("Lấy lịch sử từ minhngoc"):
    bar = st.progress(0.0)
    with st.spinner("Đang tải..."):
        hist, fails = fetch_history(end, n_days, progress=bar.progress)
    st.session_state["hist"] = hist
    if hist:
        ds = sorted(hist)
        st.success(f"Đã lấy {len(hist)} kỳ ({ds[0]} → {ds[-1]}). "
                   f"Trang lỗi/bỏ qua: {fails}.")
    else:
        st.error("Không lấy được dữ liệu. Có thể minhngoc đổi cấu trúc trang "
                 "hoặc mạng bị chặn — báo lại để cập nhật parser.")

hist = st.session_state.get("hist", {})
if hist:
    days = [hist[d] for d in sorted(hist)]
    br = base_rate()

    tab1, tab2, tab3 = st.tabs(["Tổng quan", "Kiểm định & Backtest", "Bảng lô (tần suất/gan)"])

    with tab1:
        st.metric("Base rate lý thuyết (1 số bất kỳ về/ngày)", f"{br * 100:.2f}%")
        st.write(f"Số kỳ: **{len(days)}** · mỗi kỳ 27 lô · tổng lượt: **{sum(len(d) for d in days)}**")
        st.info("Với bộ số công bằng, MỌI số đều có ~23,8%/ngày. Tần suất/gan ở tab 3 "
                "chỉ là mô tả quá khứ, không phải dự báo — tab 2 sẽ chứng minh điều đó.")

    with tab2:
        chi2, exp, thr = chi_square(days)
        st.subheader("Chi-square — bộ số có 'lệch' không?")
        st.write(f"Kỳ vọng mỗi số ≈ **{exp:.1f}** · χ² = **{chi2:.1f}** · ngưỡng 5% ≈ **{thr}**")
        st.success("χ² < ngưỡng ⇒ không có số nào lệch có ý nghĩa." if chi2 < thr
                   else "χ² > ngưỡng ⇒ có lệch, cần kiểm tra thêm (mẫu nhỏ? nguồn lỗi?).")

        st.subheader("Backtest walk-forward")
        if len(days) < 90:
            st.warning(f"Mới có {len(days)} kỳ; nên lấy ≥ 90 kỳ để backtest ổn định.")
        k = st.slider("Số con chọn mỗi ngày", 3, 15, 5)
        # warmup tự co theo dữ liệu, luôn chừa lại ngày để kiểm thử
        warmup = min(60, max(10, len(days) // 3))
        if st.button("Chạy backtest"):
            res, trials = backtest(days, k, warmup=warmup)
            n = trials * k
            if n == 0:
                st.warning(f"Chưa đủ dữ liệu để backtest (cần > {warmup} kỳ, "
                           f"hiện có {len(days)}). Hãy lấy thêm lịch sử ở phía trên.")
            else:
                st.caption(f"Khởi động {warmup} kỳ · kiểm thử trên {trials} kỳ.")
                table = []
                for name, hits in sorted(res.items(), key=lambda x: -x[1]):
                    lo, hi = wilson_ci(hits, n)
                    table.append({"Chiến lược": name,
                                  "Tỷ lệ trúng": f"{hits / n * 100:.2f}%",
                                  "KTC 95%": f"{lo * 100:.2f}–{hi * 100:.2f}%"})
                st.table(table)
                st.caption(f"Nếu KTC 95% của 'nóng'/'gan' phủ base rate {br*100:.1f}% và chồng "
                           "lên 'ngẫu nhiên' ⇒ phương pháp KHÔNG có ý nghĩa thống kê. "
                           "Đây là kết quả kỳ vọng với xổ số công bằng.")

    with tab3:
        st.caption("Bảng mô tả quá khứ (không phải dự đoán). Sắp theo số lần về giảm dần.")
        rows = sorted(current_table(days), key=lambda r: -r["Số lần về"])
        st.dataframe(rows, use_container_width=True, height=420)

st.divider()
st.caption("Nguồn: minhngoc.net.vn. Xổ số là ngẫu nhiên độc lập; không phương pháp nào "
           "đảm bảo trúng. Công cụ kiểm chứng, không phải lời khuyên đặt cược.")
