import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import time
from pypdf import PdfReader, PdfWriter
import io
import re
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI爆速読み取り(カスタム項目版)", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. 執念のJSON抽出関数
# ==========================================
def extract_json_force(text):
    text = text.strip()
    text = re.sub(r"^```json", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    text = text.strip()
    
    try:
        return json.loads(text)
    except:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try: return json.loads(match.group(0))
            except: pass
            
    try:
        if not text.endswith("}"): text += "}]}"
        return json.loads(text)
    except: pass
    return None

# ==========================================
# 3. 解析関数（カスタマイズされた抽出項目）
# ==========================================
def analyze_page(page_bytes, label):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-flash-latest" 
    
    # ★ ここが新しい抽出項目の指示書です ★
    prompt = """
    この伝票画像（1ページのみ）の「明細行」を全て読み取り、以下のJSON形式で出力してください。
    解説やMarkdownは一切不要です。
    
    【抽出のルール】
    - 得意先番号: 画像に記載が無い場合は空文字（""）にすること。
    - 得意先名: 宛名などから取得すること。
    - 行番号: 伝票ごとに1から順番に振ること。
    - 商品名: JANコードが含まれている場合は、純粋な商品名のみに分離すること。
    - 数値項目: カンマ(,)を取り除いた純粋な数値にすること。
    
    [
      {
        "slip_no": "伝票No",
        "date": "日付",
        "page_no": "ページ番号（例：1/1、1/3など）",
        "customer_code": "得意先番号",
        "customer_name": "得意先名",
        "line_no": "行番号",
        "slip_type": "伝票区分（掛売・現金など）",
        "jan_code": "JANコード",
        "product_name": "商品名（JANを除いた純粋な商品名）",
        "pack_qty": "入数",
        "unit_qty": "個数",
        "total_qty": "数量（入数×個数）",
        "unit_price": "単価（税抜）",
        "total_unit_price": "単価合計（税抜）",
        "selling_price": "売価（税抜）",
        "total_selling_price": "売価合計（税抜）"
      }
    ]
    """
    
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            content_part = {"mime_type": "application/pdf", "data": page_bytes}
            response = model.generate_content(
                [prompt, content_part],
                generation_config={"response_mime_type": "application/json"}
            )
            
            data = extract_json_force(response.text)
            if data:
                if isinstance(data, dict) and "items" in data: return {"status": "success", "data": data["items"], "label": label}
                elif isinstance(data, list): return {"status": "success", "data": data, "label": label}
                else: return {"status": "success", "data": [data], "label": label}
            
            return {"status": "parse_error", "raw": response.text[:200], "label": label}
            
        except Exception as e:
            time.sleep(2)
            continue
            
    return {"status": "api_error", "raw": "通信失敗", "label": label}

# ==========================================
# 4. メイン処理（範囲指定・5並列）
# ==========================================
st.title("⚡ AI爆速読み取りシステム (カスタム抽出版)")
st.markdown("ご指定の16項目（伝票No、得意先情報、金額詳細など）を正確に抽出します。")

uploaded_file = st.file_uploader("PDFファイルをアップロードしてください", type=["pdf"])

if uploaded_file:
    try:
        pdf_reader = PdfReader(uploaded_file)
        total_pages = len(pdf_reader.pages)
        
        st.success(f"📄 ファイル読み込み成功: 全 {total_pages} ページ")
        
        # --- ページ範囲指定UI ---
        col1, col2 = st.columns(2)
        with col1:
            start_p = st.number_input("開始ページ", min_value=1, max_value=total_pages, value=1)
        with col2:
            end_p = st.number_input("終了ページ", min_value=start_p, max_value=total_pages, value=total_pages)
            
        if st.button(f"🚀 読み取り開始（{start_p}〜{end_p}ページ）", use_container_width=True):
            
            all_rows = []
            error_log = []
            target_pages = end_p - start_p + 1
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            status_text.text(f"🚀 処理準備中... {start_p}〜{end_p} ページを展開します")
            
            tasks = []
            for page_num in range(start_p, end_p + 1):
                page_idx = page_num - 1 
                pdf_writer = PdfWriter()
                pdf_writer.add_page(pdf_reader.pages[page_idx])
                with io.BytesIO() as output:
                    pdf_writer.write(output)
                    tasks.append({"bytes": output.getvalue(), "label": f"p{page_num}"})
            
            completed_count = 0
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_task = {executor.submit(analyze_page, t["bytes"], t["label"]): t for t in tasks}
                
                for future in as_completed(future_to_task):
                    completed_count += 1
                    result = future.result()
                    label = result["label"]
                    
                    status_text.text(f"⚡ 爆速処理中... {completed_count}/{target_pages} ページ完了 ({label})")
                    progress_bar.progress(completed_count / target_pages)
                    
                    if result["status"] == "success" and result["data"]:
                        for item in result["data"]:
                            if isinstance(item, dict):
                                item["PDFページ"] = label # PDFの物理ページ番号
                                all_rows.append(item)
                    else:
                        raw_data = result.get("raw", "理由不明")
                        error_log.append(f"{label} - 読み取り失敗 ({raw_data})")
                    
                    gc.collect()
            
            status_text.success(f"🎉 完璧です！{start_p}〜{end_p}ページの処理が完了しました！")
            
            if error_log:
                with st.expander(f"⚠️ 一部読み取れなかった箇所 ({len(error_log)}件)"):
                    for err in error_log:
                        st.write(err)
            
            if all_rows:
                df = pd.DataFrame(all_rows)
                
                # ページ番号順に並び替え
                try:
                    df['sort_key'] = df['PDFページ'].str.replace('p', '').astype(int)
                    df = df.sort_values(['sort_key', 'line_no']).drop('sort_key', axis=1) # ページ順 ＆ 行番号順
                except:
                    pass
                
                # ★ CSVに出力する列の順序と日本語名をマッピング ★
                cols = [
                    "PDFページ", "slip_no", "date", "page_no", "customer_code", "customer_name",
                    "line_no", "slip_type", "jan_code", "product_name", "pack_qty", "unit_qty",
                    "total_qty", "unit_price", "total_unit_price", "selling_price", "total_selling_price"
                ]
                col_map = {
                    "slip_no": "伝票No", "date": "日付", "page_no": "ページ番号",
                    "customer_code": "得意先番号", "customer_name": "得意先名",
                    "line_no": "行番号", "slip_type": "伝票区分", "jan_code": "JANコード",
                    "product_name": "商品名", "pack_qty": "入数", "unit_qty": "個数",
                    "total_qty": "数量", "unit_price": "単価(税抜)",
                    "total_unit_price": "単価合計(税抜)", "selling_price": "売価(税抜)",
                    "total_selling_price": "売価合計(税抜)"
                }
                
                # 存在する列だけ残してリネーム
                valid_cols = [c for c in cols if c in df.columns]
                df = df[valid_cols].rename(columns=col_map)
                
                st.subheader(f"📊 抽出データ ({len(df)}行)")
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"CSVを保存 💾", 
                    data=csv, 
                    file_name=f"custom_data_p{start_p}-{end_p}.csv", 
                    mime="text/csv"
                )
            else:
                st.error("データを抽出できませんでした。")
                
    except Exception as e:
        st.error(f"システムエラー: {e}")
