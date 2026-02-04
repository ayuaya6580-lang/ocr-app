import streamlit as st
import google.generativeai as genai
from PIL import Image
import json
import pandas as pd
import time

# ==========================================
# 1. アプリの設定
# ==========================================
st.set_page_config(page_title="AI一括伝票読み取り", layout="wide")

try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数
# ==========================================
def analyze_document(input_data, mime_type):
    genai.configure(api_key=GOOGLE_API_KEY)
    model_name = "gemini-1.5-flash" # 高速処理向き

    prompt = """
    以下のレシート・納品書・請求書を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法は不要です。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票合計金額: 数値のみ)
    - invoice_number (インボイス番号)
    
    【明細リスト (items)】
    - jan_code (JAN/品番)
    - product_name (商品名)
    - quantity (数量: 数値)
    - cost_price (単価/下代: 数値)
    - line_total (金額/行合計: 数値)
    """
    
    try:
        model = genai.GenerativeModel(model_name)
        
        # API制限回避のための短い待機
        time.sleep(1) 

        if mime_type == "application/pdf":
            content_part = {"mime_type": "application/pdf", "data": input_data}
            response = model.generate_content([prompt, content_part])
        else:
            response = model.generate_content([prompt, input_data])

        text = response.text
        cleaned_text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned_text)
            
    except Exception as e:
        return None # エラー時はスキップ

# ==========================================
# 3. 画面のデザイン (一括処理UI)
# ==========================================
st.title("📂 AI伝票一括読み取りシステム")
st.markdown("フォルダ内のファイルを**まとめてドラッグ＆ドロップ**してください。一気に処理して1つの表にまとめます。")

# 複数ファイルを受け付ける設定 (accept_multiple_files=True)
uploaded_files = st.file_uploader(
    "ここにファイルをまとめて放り込んでください (画像・PDF)", 
    type=["jpg", "png", "jpeg", "pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    file_count = len(uploaded_files)
    st.info(f"📄 {file_count} 件のファイルが選択されました")

    if st.button(f"一括読み取り開始 ({file_count}件) 🚀", use_container_width=True):
        
        # 結果を溜めておくリスト
        all_rows = []
        
        # プログレスバー（進捗状況）を表示
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 1つずつ順番に処理
        for i, file in enumerate(uploaded_files):
            status_text.text(f"処理中... {i+1} / {file_count} : {file.name}")
            
            # ファイルの種類判定
            file_bytes = file.getvalue()
            mime_type = "application/pdf" if file.type == "application/pdf" else "image"
            if mime_type == "image":
                file_bytes = Image.open(file)

            # AI解析実行
            result = analyze_document(file_bytes, mime_type)
            
            if result:
                # 共通ヘッダー情報
                header_info = {
                    "ファイル名": file.name,
                    "日付": result.get("date"),
                    "仕入先": result.get("company_name"),
                    "伝票合計": result.get("total_amount"),
                    "インボイスNo": result.get("invoice_number"),
                }
                
                # 明細がある場合
                items = result.get("items", [])
                if items:
                    for item in items:
                        row = header_info.copy()
                        row.update({
                            "JAN/品番": item.get("jan_code"),
                            "商品名": item.get("product_name"),
                            "数量": item.get("quantity"),
                            "単価(下代)": item.get("cost_price"),
                            "金額(行合計)": item.get("line_total")
                        })
                        all_rows.append(row)
                else:
                    # 明細なしの場合
                    row = header_info.copy()
                    row.update({"商品名": "（明細なし）"})
                    all_rows.append(row)
            
            # 進捗バーを更新
            progress_bar.progress((i + 1) / file_count)

        status_text.success("すべての処理が完了しました！")
        
        # --- 結果表示とダウンロード ---
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            # 列の整理
            desired_order = [
                "ファイル名", "日付", "仕入先", "JAN/品番", "商品名", 
                "数量", "単価(下代)", "金額(行合計)", "伝票合計", "インボイスNo"
            ]
            final_columns = [c for c in desired_order if c in df.columns]
            df = df[final_columns]
            
            st.subheader("📊 統合データ")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="全データをCSVで保存 💾",
                data=csv,
                file_name="bulk_data.csv",
                mime="text/csv",
                key="download-csv"
            )
        else:
            st.warning("データを読み取れませんでした。")
