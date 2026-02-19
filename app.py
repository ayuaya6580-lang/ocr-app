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
st.set_page_config(page_title="AIハイブリッド一括読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。")
    st.stop()

# ==========================================
# 2. ユーティリティ関数（JSON抽出・掃除）
# ==========================================
def extract_json_safe(text):
    """
    AIの返答からJSON部分を執念深く抜き出す
    """
    text = text.strip()
    # 1. マークダウン削除
    text = text.replace("```json", "").replace("```", "")
    
    # 2. [ ... ] または { ... } の範囲を抽出
    match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if match:
        text = match.group(0)
    
    try:
        return json.loads(text)
    except:
        # 3. 閉じ括弧が足りない場合の補正（簡易）
        try:
            if text.startswith("[") and not text.endswith("]"):
                return json.loads(text + "]")
            if text.startswith("{") and not text.endswith("}"):
                return json.loads(text + "}")
        except:
            pass
    return None

# ==========================================
# 3. 解析関数（テキストモード & 画像モード）
# ==========================================
def analyze_content(content, mode, source_label):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-1.5-flash" # 安定版

    if mode == "text":
        prompt = f"""
        以下の「テキストデータ（PDFから抽出）」から、請求書・納品書の明細行を探し出し、JSONリストに変換してください。
        
        【テキストデータ】
        {content}
        
        【出力ルール】
        - 余計な解説は不要。JSONのみ出力。
        - 以下のキーを使用: date, company_name, product_name, quantity, cost_price(単価), line_total(金額), invoice_number
        """
        input_data = prompt
    else:
        # 画像/PDFモード
        prompt = """
        画像を読み取り、明細行をJSONリストのみで出力してください。
        キー: date, company_name, product_name, quantity, cost_price, line_total, invoice_number
        """
        input_data = [prompt, content]

    # リトライ処理
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(model_name)
            
            if mode == "text":
                response = model.generate_content(input_data)
            else:
                # PDF/画像
                response = model.generate_content(input_data)

            return {"raw": response.text, "data": extract_json_safe(response.text)}

        except Exception as e:
            time.sleep(2 * (attempt + 1))
            if attempt == 2:
                return {"error": str(e)}
    
    return None

# ==========================================
# 4. メイン処理
# ==========================================
st.title("🛡️ AIハイブリッド一括読み取り (Text & Image)")
st.markdown("PDFの**文字データ**を直接読み取ることで、高速かつ正確に処理します。（画像のみの場合は自動切替）")

uploaded_files = st.file_uploader(
    "ファイルをドラッグ＆ドロップ", 
    type=["pdf", "jpg", "png"], 
    accept_multiple_files=True
)

if uploaded_files:
    if st.button(f"読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        debug_logs = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        tasks = []
        
        # --- 準備：タスク生成 ---
        status_text.text("ファイルを解析中...")
        
        for file in uploaded_files:
            if file.type == "application/pdf":
                try:
                    pdf_reader = PdfReader(file)
                    for i, page in enumerate(pdf_reader.pages):
                        # ★ここが新機能：テキストが抽出できるか試す
                        extracted_text = page.extract_text()
                        
                        if len(extracted_text) > 50: # 50文字以上あれば「テキストモード」でいく
                            tasks.append({
                                "type": "text",
                                "content": extracted_text,
                                "label": f"{file.name} (p{i+1}) [Text]"
                            })
                        else:
                            # テキストがなければ「画像モード（PDF切り出し）」
                            writer = PdfWriter()
                            writer.add_page(page)
                            with io.BytesIO() as output:
                                writer.write(output)
                                pdf_bytes = output.getvalue()
                            
                            tasks.append({
                                "type": "pdf_image",
                                "content": {"mime_type": "application/pdf", "data": pdf_bytes},
                                "label": f"{file.name} (p{i+1}) [Img]"
                            })
                except:
                    debug_logs.append(f"{file.name}: 読み込みエラー")
            else:
                # 画像ファイル
                tasks.append({
                    "type": "image",
                    "content": Image.open(file),
                    "label": f"{file.name} [Img]"
                })

        total_tasks = len(tasks)
        st.write(f"処理対象: {total_tasks} ページ")

        # --- 実行フェーズ（5並列） ---
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_task = {
                executor.submit(analyze_content, t["content"], t["type"], t["label"]): t 
                for t in tasks
            }
            
            completed = 0
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                completed += 1
                progress_bar.progress(completed / total_tasks)
                status_text.text(f"処理中... {completed}/{total_tasks}: {task['label']}")
                
                try:
                    result = future.result()
                    
                    if result and "error" in result:
                        debug_logs.append(f"❌ {task['label']}: エラー {result['error']}")
                    elif result and result.get("data"):
                        # 成功！
                        data = result["data"]
                        # リストか辞書かで分岐
                        items = data if isinstance(data, list) else data.get("items", [])
                        
                        if not items and isinstance(data, dict):
                             # itemsキーがない辞書の場合、それを単体データとして扱う
                             items = [data]

                        for item in items:
                            if isinstance(item, dict):
                                row = {
                                    "ページ": task['label'],
                                    "日付": item.get("date"),
                                    "仕入先": item.get("company_name"),
                                    "商品名": item.get("product_name"),
                                    "数量": item.get("quantity"),
                                    "単価": item.get("cost_price"),
                                    "金額": item.get("line_total"),
                                    "インボイス": item.get("invoice_number")
                                }
                                all_rows.append(row)
                    else:
                        # 解析はできたがJSONにならなかった場合（生ログに残す）
                        raw_text = result.get("raw", "")[:200] if result else "None" # 長すぎるので先頭だけ
                        debug_logs.append(f"⚠️ {task['label']}: データ抽出失敗 (AI回答: {raw_text}...)")

                except Exception as e:
                    debug_logs.append(f"❌ {task['label']}: システムエラー {e}")

        status_text.success("完了！")

        # --- デバッグ情報の表示 ---
        if debug_logs:
            with st.expander(f"⚠️ 解析ログ ({len(debug_logs)}件 - クリックして確認)"):
                st.write("「データ抽出失敗」が多い場合、AIがJSON以外の形式で返事をしています。")
                for log in debug_logs:
                    st.text(log)

        # --- 結果表示 ---
        if all_rows:
            df
