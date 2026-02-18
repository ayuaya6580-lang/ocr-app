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

# APIキーの読み込み（金庫から）
try:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    st.error("❌ APIキーが設定されていません。StreamlitのSecretsを設定してください。")
    st.stop()

# ==========================================
# 2. 解析を行う関数（裏方の処理）
# ==========================================
def analyze_document_safe(input_data, mime_type):
    # API設定
    genai.configure(api_key=GOOGLE_API_KEY)
    
    # ★あなたの環境で動くモデル名
    model_name = "gemini-flash-latest" 

    prompt = """
    以下のレシート・納品書・請求書を読み取り、純粋なJSON形式のみを出力してください。
    Markdown記法（```json 等）は含めないでください。
    
    【全体情報】
    - date (日付: YYYY-MM-DD)
    - company_name (仕入先・店名)
    - total_amount (伝票合計金額: 数値のみ)
    - invoice_number (インボイス番号)
    
    【明細リスト (items)】
    - jan_code (JAN/品番)
    - product_name (商品名)
    - quantity (数量: 数値)
    - retail_price (上代/定価: 数値)
    - cost_price (単価/下代: 数値)
    - line_total (金額/行合計: 数値)
    - wholesale_rate (掛け率)
    """
    
    # エラー時の再挑戦回数
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            
            # PDFと画像でデータの渡し方を変える
            if mime_type == "application/pdf":
                content_part = {"mime_type": "application/pdf", "data": input_data}
                response = model.generate_content([prompt, content_part], request_options={"timeout": 600})
            else:
                response = model.generate_content([prompt, input_data], request_options={"timeout": 600})

            text = response.text
            # JSON整形
            cleaned_text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned_text)

        except Exception as e:
            # 混雑エラーなら待機
            error_msg = str(e)
            if "429" in error_msg or "503" in error_msg:
                time.sleep(10 * (attempt + 1))
                continue
            elif "404" in error_msg:
                 return None
            else:
                return None
    
    return None

# ==========================================
# 3. 画面のデザイン (UI)
# ==========================================
# ★ここからインデントを戻します（左端に寄せる）

st.title("📂 AI伝票一括読み取りシステム")
st.markdown("フォルダ内のファイルを**まとめてドラッグ＆ドロップ**してください。")

# ★ここが「口」を作る部分です
uploaded_files = st.file_uploader(
    "ここにファイルをまとめて放り込んでください (画像・PDF)", 
    type=["jpg", "png", "jpeg", "pdf"], 
    accept_multiple_files=True
)

# ファイルがアップロードされたら処理開始ボタンを出す
if uploaded_files:
    file_count = len(uploaded_files)
    st.info(f"📄 {file_count} 件のファイルがセットされました")

    if st.button(f"一括読み取り開始 🚀", use_container_width=True):
        
        all_rows = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        error_log = []
        
        # 1つずつ処理
        for i, file in enumerate(uploaded_files):
            status_text.text(f"⏳ 処理中... {i+1}/{file_count} : {file.name}")
            
            # 安全のため3秒休憩
            time.sleep(3)

            try:
                # ファイルの準備
                file_bytes = file.getvalue()
                mime_type = "application/pdf" if file.type == "application/pdf" else "image"
                if mime_type == "image":
                    file_bytes = Image.open(file)
                
                # AI解析実行
                result = analyze_document_safe(file_bytes, mime_type)
                
                if result:
                    # 成功データの保存
                    header_info = {
                        "ファイル名": file.name,
                        "日付": result.get("date"),
                        "仕入先": result.get("company_name"),
                        "伝票合計": result.get("total_amount"),
                        "インボイスNo": result.get("invoice_number"),
                    }
                    items = result.get("items", [])
                    if items:
                        for item in items:
                            row = header_info.copy()
                            row.update({
                                "JAN/品番": item.get("jan_code"),
                                "商品名": item.get("product_name"),
                                "数量": item.get("quantity"),
                                "上代": item.get("retail_price"),
                                "単価(下代)": item.get("cost_price"),
                                "金額(行合計)": item.get("line_total"),
                                "掛け率": item.get("wholesale_rate")
                            })
                            all_rows.append(row)
                    else:
                        row = header_info.copy()
                        row.update({"商品名": "（明細なし）"})
                        all_rows.append(row)
                else:
                    error_log.append(f"{file.name} (読み取り失敗)")
            
            except Exception as e:
                error_log.append(f"{file.name} (エラー: {e})")

            # 進捗バー更新
            progress_bar.progress((i + 1) / file_count)

        status_text.success("完了！")

        # 結果表示
        if error_log:
            with st.expander("⚠️ エラーがあったファイル"):
                for err in error_log:
                    st.write(err)
            
        if all_rows:
            df = pd.DataFrame(all_rows)
            
            # 列の整理
            desired_order = [
                "ファイル名", "日付", "仕入先", "JAN/品番", "商品名", 
                "数量", "上代", "掛け率", "単価(下代)", "金額(行合計)", "伝票合計", "インボイスNo"
            ]
            final_columns = [c for c in desired_order if c in df.columns]
            df = df[final_columns]
            
            st.subheader("📊 統合データ")
            st.dataframe(df)
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="CSV保存 💾",
                data=csv,
                file_name="bulk_data_final.csv",
                mime="text/csv"
            )
