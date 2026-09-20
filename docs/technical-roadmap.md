# 技術路線

本文是「掃描 → 字型」整條管線的設計說明，目標是讓任何人在一台能連上
Wikimedia Commons 的機器上，照著本文把管線從零跑到 v0.1。各節先講原理與決策，
再列資料格式與驗收標準。規劃中的腳本名稱與 `AGENTS.md` 一致。

## 0. 核心原理：流水號對位

`third_party/unicode/ucd/SealSources.txt` 替每個篆書碼位記錄它在四個《說文》版本
中的**流水號**（`kSEAL_CCZSrc` 為 `C-ddddd`，其餘同理）。流水號是該版本中篆字依
閱讀順序的序號，正篆與重文都算。因此：

1. 把一個版本的所有篆字按閱讀順序切出來並編號；
2. 第 N 個切片就是流水號 N 的字；
3. 用 `SealSources.txt` 反查碼位，再用 `kSEAL_MCJK` 得到楷書字頭做核對。

整條管線就是把這三步做穩。最大風險是**漏切或多切一個篆字會讓後面全部錯位**，
所以第 3 步的核對不是選配，而是對位演算法本身的一部分（見第 4 節的序列對齊）。

各版本收字數（來自 `verify_seal_sources.py`）：THX 11,328、CCZ 11,090、
QJZ 10,703、DYC 10,683。以陳昌治本（CCZ）為主底本時，有 238 個碼位必須回退到
其他版本，`SealSources.txt` 裡沒有 `kSEAL_CCZSrc` 的那些就是。

## 1. 底本與取頁

### 1.1 決策

- 主底本：**陳昌治本**（同治十二年番禺陳昌治刻本，一篆一行）。每個正篆獨佔一行
  頂端、版面規整、有界欄，切字最容易。
- 回退順序：陳本缺 → 藤花榭本（THX，收字最全）→ 段注本（DYC）→ QJZ 本。
  四個縮寫依 UAX #60：THX 藤花榭本、CCZ 陳昌治本、DYC 段玉裁注本；QJZ 依縮寫
  應為祁寯藻本《說文解字繫傳》（小徐本），待能連上 unicode.org 時對照 UAX #60
  確認（OpenCC 的 NEWS 寫作汲古閣本，需一併核對）。每個字形的實際版本記錄在
  provenance。
- 來源只用 Wikimedia Commons 上的公版掃描，以 **Commons 檔名 + 頁碼** 定位。
  不下載整本 PDF/DjVu，只抓需要的頁面渲染圖；不把任何掃描檔提交進倉庫。

### 1.2 取頁方式

MediaWiki API 可以直接要某一頁的指定寬度渲染圖：

```
https://commons.wikimedia.org/w/api.php
  ?action=query&format=json&formatversion=2
  &prop=imageinfo&iiprop=url|size|sha1|mime
  &iiurlwidth=2400&iiurlparam=page12-2400px
  &titles=File:<檔名>.pdf
```

回應中的 `imageinfo[0].thumburl` 就是第 12 頁、寬 2400 px 的 JPEG。`pagecount`
欄位給總頁數。另一個等價入口是
`Special:Redirect/file/<檔名>?page=12&width=2400`。`scripts/fetch_pages.py`
實作前者，並：

- 依 Commons 的機器人政策帶自訂 `User-Agent`，請求間隔 ≥ 1 秒，失敗指數退避；
- 快取到 `sources/cache/<edition>/<file-slug>/p0012-w2400.jpg`，以
  `sources/cache/index.json` 記錄每頁的來源 URL、原檔 sha1、寬度、下載時間；
- 支援 `--dry-run` 只列 URL，`--info` 只查頁數。

渲染寬度建議 2400–3000 px：陳本半葉高約 20 cm，篆字約 1.5 cm 高，2400 px 寬的
整葉圖裡一個篆字約 120 px 高，potrace 需要至少 100 px 才能得到平滑輪廓。若某檔
的原始解析度更高可提高寬度；寬度記進 provenance。

### 1.3 manifest 格式

`sources/manifest.yaml` 是**唯一**記錄掃描來源的地方：

```yaml
editions:
  ccz:                                  # 版本代號，對應 provenance 的 edition 欄
    name: 陳昌治本《說文解字》十五卷（同治十二年番禺陳昌治校刊）
    source_property: kSEAL_CCZSrc       # SealSources.txt 中的屬性名
    sequence_prefix: "C-"
    priority: 1                         # 回退順序，越小越優先
    files:
      - commons_title: "File:XXXX 說文解字 第1冊.pdf"
        institution: 國家圖書館（中國）掃描，經 Wikimedia Commons
        reuse_terms: "Public domain; 見 Commons 檔案頁的授權模板"
        render_width: 2400
        image_kind: spread              # spread（一圖兩半葉）或 half_leaf（一圖一半葉）
        pages:
          - first: 7                    # 1-based PDF 頁碼
            last: 63
            juan: 卷一上
            note: 第 7 頁自「一部」起
          - first: 64
            last: 118
            juan: 卷一下
        skip_pages: [40]                # 空白頁、書名頁、掃描重複頁
```

`image_kind` 決定切半葉的方式。不論是一葉展開（a 面在右、b 面在左）還是翻開的跨頁
（右邊是前一葉 b 面、左邊是後一葉 a 面），閱讀順序都是**先右半、後左半**，半葉內
欄位由右至左。若某檔已把半葉拆成單張，填 `half_leaf`。

## 2. 頁面前處理（`segment_pages.py` 第一段）

1. 讀取快取圖，轉灰階。
2. **去斜**：以霍夫變換找界欄與版框的近垂直長線，取角度中位數旋轉校正。木刻本界欄
   清楚，這比文字投影法穩。
3. **二值化**：Sauvola（窗 31–51 px，k≈0.2），避免全域門檻在紙色不均處失效。
4. **切半葉**：找版心（中縫的魚尾／書名列）或圖中央的空白帶，切成右、左兩半。
5. **切欄**：垂直投影找界欄位置；陳本每半葉欄數固定，manifest 可填 `columns`
   當作驗證值，偵測結果與之不符就把該頁標為需人工確認。
6. 版框外的內容（書口、頁碼、手寫批註）一律丟棄。

每頁輸出 `build/pages/<edition>/<slug>/p0012.json`：旋轉角、半葉與欄的座標，供後續
階段與審核工具使用。

## 3. 篆字偵測（`segment_pages.py` 第二段）

陳本一欄的結構是：頂端一個大篆字，其下為楷書說解（雙行小字），說解中會**行內**出現
重文篆字與「某某切」等文字。要抓的是：

- **行首正篆**：欄內最上方、面積最大的連通元件群。做法是取欄頂端往下的水平投影，
  第一段連續墨跡即為篆字區；再以連通元件的外接框合併（篆字常斷成多個元件）。
- **行內重文**：混在小字裡，尺寸與小字接近，不能靠大小。需要一個「篆 vs 楷」
  二分類器。

分類器策略：

1. 先用行首正篆當正樣本、明顯的小字楷書當負樣本，自動得到數千筆標註。
2. 特徵用 HOG + 筆畫寬度統計（篆書筆畫等寬、轉折圓、無楷書的頓挫與尖角），
   先跑 scikit-learn 的邏輯迴歸或 SVM；不夠再上小型 CNN。
3. 在小字區以滑動視窗＋非極大值抑制找候選，過分類器。
4. 分類器輸出分數進 provenance，低信心的進人工審核。

每個偵測到的篆字輸出：頁、半葉、欄、閱讀順序序號、外接框、種類（headword/inline）、
分類分數。**每頁篆字數量**寫進頁面 JSON，供第 4 節核對。

## 4. 對位（`align_sequence.py`）

單純計數（第 N 個切片 = 流水號 N）在整本書上一定會累積錯誤，所以用**序列對齊**：

1. 由 `SealSources.txt` 產生該版本的期望序列：`[(流水號, 碼位, 楷書字頭)]`。
2. 對每個偵測到的篆字，OCR 其緊鄰的楷書字頭（陳本正篆之下第一個大字即為楷書
   字頭；重文旁則是「古文／籒文／或从某」等說明，OCR 到的字可能是部件）。OCR 用
   tesseract `chi_tra` + `chi_tra_vert`，或 PaddleOCR；《說文》有大量罕用字 OCR
   認不出，沒關係，只要一部分能當**錨點**即可。
3. 以 Needleman–Wunsch 把「偵測序列」對齊到「期望序列」：OCR 相符給高分，
   不相符給零分而非負分（因為 OCR 會錯），插入／刪除給負分。這樣少切、多切一個
   篆字只會在局部造成 gap，不會讓整卷錯位。
4. 卷首、部首（`kSEAL_Rad` 的首字）與每部的字數是天然的硬錨點：部首字在陳本是
   「凡某之屬皆从某」前的那個字，可以再用規則核對。
5. 對齊結果分三類寫入 `data/provenance/glyphs.csv`：
   `aligned`（有錨點支持）、`inferred`（靠鄰居推得、無 OCR 支持）、
   `conflict`（gap 或 OCR 矛盾）。`conflict` 與連續過長的 `inferred` 進審核。

驗收：每卷結束時，偵測總數與期望總數之差為 0；`conflict` 全部經人工處理；
`inferred` 連續長度 ≤ 20。

## 5. 向量化與正規化（`trace_glyphs.py`）

1. 依外接框裁切原灰階圖，外擴 8 px，放大到高 600 px（雙三次），再二值化一次。
   放大後再二值化比直接對低解析度二值圖描邊平滑得多。
2. 去噪：面積 < (筆畫寬)² × 0.3 的元件視為木刻毛邊或紙紋，刪除；但要保留篆書的
   短點畫，所以門檻用該字的筆畫寬估計值（距離變換的中位數）而非固定像素。
3. potrace：`turdsize` 依上一步門檻換算，`alphamax 1.0`，`opttolerance 0.2`。
   可用系統 `potrace` 二進位或純 Python 的 `potracer`；輸出 SVG path。
4. **正規化到字身框**：UPM 1000，ascender 880、descender −120。篆書字形偏長，
   以高度為主縮放：字形高縮到 760 單位，垂直置中於 [−60, 820]，水平置中於 1000
   寬度；寬度超過 880 時改以寬度縮放。縮放係數與原始像素高記進 provenance，
   保留木刻本中相對大小的資訊供日後統一。
5. 每字一檔 `glyphs/u3D000.svg`，`viewBox="0 -880 1000 1000"`（y 向下，配合
   fontTools 讀入時翻轉）。檔案由腳本產生但**提交進倉庫**，方便 review diff。
6. 若 `data/overrides/u3D000.svg` 存在，建字型時以它取代。

第二版才做的**筆畫規整化**：對二值圖做骨架化，估計每筆寬度，以統一寬度、圓頭
重新描筆再取輪廓。第一版刻意保留影印風，先求全、求對。

## 6. 建字型（`build_font.py`）

- 工具：fontTools + ufoLib2 + fontmake，全部純 Python，不依賴 FontForge。
- 流程：讀 `glyphs/`（套用 overrides）→ 以 `fontTools.svgLib.path.SVGPath`
  畫進 UFO glyph（三次曲線）→ fontmake 產 OTF（CFF）與 TTF（cu2qu 轉二次）。
- glyph 名 `uXXXXX`；cmap 需含 format 12 子表（Plane 3）。fontTools 對任何碼位
  都不需要 Unicode 資料表支援，工具鏈不會因 Unicode 18.0 太新而卡住。
- 名稱：家族名 `Kaiyuan Small Seal`，樣式 `Regular`，版本號跟 git tag。
  OFL Reserved Font Name 待決。
- **相容版**：同一份 UFO 換 cmap。對照由 `SealSources.txt` 的 `kSEAL_MCJK` 反轉而
  來；一個楷書字對到多個篆字時預設取碼位最小者（即《說文》正篆），例外寫在
  `data/compat_prefer.txt`，內容與 OpenCC `SealCharacters.txt` 的
  `@reverse-prefer` 行保持一致。相容版家族名加 `Compat` 後綴。
- 建置後檢查（`tests/`）：11,328 碼位齊全；空 glyph 必須在 `data/corrections.csv`
  有理由；外接框在字身框內；沒有兩個 glyph 輪廓完全相同（相同通常代表切字重複）。

## 7. 校樣與審核

- `proof_sheets.py` 依 `kSEAL_Rad` 分部首產生 HTML：每字一格，並排顯示建好的
  字型渲染（`@font-face` 載入 `build/` 的 TTF）、原掃描切片、楷書字頭、provenance
  狀態。切片在本機由快取現場產生，不提交；`docs/` 只放少量示意圖。
- 審核清單：`align_sequence.py` 產出的 `conflict`／低信心項目，以及建置檢查抓到的
  外接框異常、重複輪廓。審核結果寫入 `data/corrections.csv`：

```
codepoint,action,edition,commons_title,page,x,y,w,h,reason
3D0A7,recrop,ccz,"File:...pdf",23,1180,410,96,130,行首篆字與界欄相連
3D1F2,drop,,,,,,,,陳本此字漫漶，改用 thx
3E00C,empty,,,,,,,,四本皆殘，暫留空 glyph
```

`recrop` 指定新的裁切框，`drop` 表示改用下一順位版本，`empty` 允許空 glyph 並留
理由。手動修過輪廓的字直接放 `data/overrides/`。

## 8. 環境與依賴

- Python ≥ 3.11，虛擬環境安裝 `requirements.txt`。
- 系統工具（可選）：`potrace`（沒有就用 `potracer`）、`tesseract` 與 `chi_tra`、
  `chi_tra_vert` 語言包（沒有就先跑不含 OCR 的純計數對位，之後補）。
- 完整跑一遍的資料量：約 1,100 頁渲染圖 × 1 MB，快取約 1–2 GB；`glyphs/` 約
  11,328 個 SVG，平均 3–5 KB，總量 50 MB 以內，可以提交。

## 9. 里程碑與驗收

| 版本 | 內容 | 驗收 |
| --- | --- | --- |
| M0 | manifest 填好陳本檔名與卷頁；`fetch_pages.py` 抓回卷一 | 快取中有卷一全部頁面，`index.json` 完整 |
| M1 | 卷一「一部」端到端：切字、對位、向量化、建字型、校樣 | 一部的正篆與重文（一、弌、元、天、丕、吏……）在字型中正確顯示，provenance 齊全 |
| M2 | 陳本全書自動跑完 | 各卷計數差為 0；產出審核清單與 QA 報告（OCR 命中率、conflict 數） |
| M3 | 審核清單清零；238 個陳本缺字以回退版本補齊 | 11,328 碼位無未處理項目 |
| v0.1 | 影印版釋出：主字型 + 相容版 + OFL + 校樣 | CI 從乾淨 checkout 重建成功並通過 `tests/` |
| v0.2 | 筆畫規整化、字面統一、社群修正回收 | 另立設計文件 |

## 10. 待維護者決定

- 陳昌治本在 Commons 上使用哪一份掃描（檔名、冊數對應卷數）；本沙箱連不上
  Commons，尚未填入。
- 腳本授權（傾向 MIT）與 OFL Reserved Font Name。
- OCR 引擎（tesseract 最省事；PaddleOCR 對罕用字辨識率通常較高但依賴重）。

## 附錄：SVG 平滑度優化

掃描古籍木刻版的篆字輪廓往往有鋸齒感。以下是優化建議：

### 問題根源

1. 掃描影像邊界不夠清晰（木刻毛邊、紙張紋理）
2. potrace 的角度平滑參數過於保守（`alphamax=1.0`）
3. 路徑優化容差設定（`opttolerance=0.2`）可進一步調小

### 改進方案（建議順序）

#### 方案 A：調整 potrace 參數（首先嘗試）

在 `scripts/trace_glyphs.py` 裡改：

```python
# 替換原有的 potrace 調用
potrace_args = [
    'potrace', input_png,
    '-s',                      # SVG 輸出
    '--alphamax', '1.5',       # 改從 1.0 → 1.5（平滑角度）
    '--opttolerance', '0.2',   # 保持不變或改 0.15
    '--turdsize', str(turd_size),
    '-o', output_svg
]
subprocess.run(potrace_args, check=True)
```

**參數說明**：
- `alphamax=1.0`：保守，保留細節但易有尖角（鋸齒感）
- `alphamax=1.5`：**推薦**，平滑度與細節平衡
- `alphamax=2.0–2.5`：高度平滑，但細節可能丟失

#### 方案 B：提高輸入影像解析度

```python
# 在放大段改 600 → 800–1000
target_height_px = 800  # 或 1000
scale_factor = target_height_px / original_height
enlarged = cv2.resize(
    binary_crop, None,
    fx=scale_factor, fy=scale_factor,
    interpolation=cv2.INTER_CUBIC
)
```

#### 方案 C：影像前處理優化

加 morphological smoothing：

```python
# 在 Sauvola 二值化後加
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
```

#### 方案 D：後處理平滑（fontTools）

```python
# 讀 SVG 後用 fontTools 路徑簡化
from fontTools.svgLib.path import SVGPath
from fontTools.misc.psCharStrings import T2CharString

path = SVGPath.fromstring(svg_data)
# 簡化並平滑
simplified = path.simplify(tolerance=0.5)
```

### 推薦方案組合

| 優先級 | 方案 | 成本 | 效果 |
| --- | --- | --- | --- |
| 1️⃣ | A（alphamax 1.5） | 改一個參數 | ⭐⭐⭐ 顯著改善 |
| 2️⃣ | B（800px 輸入） | 改一個參數 | ⭐⭐⭐ 清晰度提升 |
| 3️⃣ | C（morphological） | 幾行程式碼 | ⭐⭐ 細節平滑 |
| 4️⃣ | D（fontTools） | 後處理 | ⭐⭐ 精細調整 |

### 驗證方法

1. 對一個 page 試跑改進參數
2. 對比 `glyphs/u3D000.svg` 的邊界（用瀏覽器放大檢查）
3. 若效果好，更新 `trace_glyphs.py` 並提交測試

### 古籍版本差異

- **陳昌治本**：版面規整，邊界清晰，用 `alphamax=1.5` 夠
- **藤花榭本**：稍有損傷，建議 `alphamax=1.5–2.0`
- **段注本**：紙張褪色，可能需 `alphamax=2.0` 或前處理優化

## 附錄二：社群現況與競合分析

### 現有篆字項目狀況（截至 2026 年 9 月）

1. **崇羲篆體**（李民翰，小學堂計畫）
   - 11,596 字（《說文解字》+常用字表）
   - CC BY-ND 授權（禁止改作）
   - 手工設計，字形優美、規整
   - 缺點：字數少於 Unicode 18.0 Seal（11,328 > 11,596 說法有誤，實際需核對），改作受限

2. **全字庫說文解字體**（中華民國國發會）
   - 政府製作，OFL 1.1 / 政府資料開放雙授權
   - 字型掛在 CJK 碼位（不在篆書區塊 U+3D000）
   - 質量穩定，可作發行前相容版的參考

3. **Unicode 18.0 代碼表圖示**（提案方，UCWG）
   - 僅供代碼表視覺示意，非正式字型
   - 版權狀態不明，**禁止參考**

### 本項目的戰略定位

| 維度 | 本項目 | 崇羲篆體 | 全字庫篆體 |
| --- | --- | --- | --- |
| 字數 | 11,328（Unicode 18.0 全覆蓋） | 11,596 | N/A（CJK 碼位） |
| 碼位 | U+3D000–U+3FC3F（篆書區塊） | CJK+兼容 | CJK 碼位 |
| 授權 | OFL 1.1（自由修改） | CC BY-ND（禁止改作） | OFL 1.1 / 政府開放 |
| **來源** | **公版古籍掃描追跡** | 手工設計 | 未知 |
| 正篆/重文 | ✓ 同時收錄 | ✓ | ? |
| 出處可追溯 | ✓ 版本、頁、框 | ✗ | ✗ |
| 機械化管線 | ✓ 完全可重跑 | ✗ | ✗ |

### 合作與社群策略

1. **不競爭，互補**：
   - 崇羲篆體專精於設計與美感；本項目專精於完整覆蓋與可溯源性
   - 用戶可根據需求選用（審美 vs 完整性）

2. **可能的合作點**：
   - 向崇羲篆體作者回報 Unicode 18.0 新增字形的手工審核建議
   - 若崇羲篆體後續支援篆書區塊，可互相參考品質標準

3. **與 OpenCC 同步**：
   - 本項目的篆字碼位選擇與 OpenCC `SealCharacters.txt` 保持一致
   - 兩個項目共用 `SealSources.txt` 與驗收標準

### 維基資源參考

中文維基百科用戶「魔琴」維護的[小篆字體資料頁](https://zh.wikipedia.org/wiki/User:%E9%AD%94%E7%90%B4/%E8%B5%84%E6%96%99/%E5%B7%B2%E7%9F%A5%E7%9A%84%E5%B0%8F%E7%AF%86%E5%AD%97%E4%BD%93)
列舉了業界現況。本項目填補了「Unicode 18.0 全字、公版源、機械化、可溯源」的空隙。
