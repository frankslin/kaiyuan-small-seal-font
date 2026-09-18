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

MediaWiki API 可以要某一頁的渲染圖：

```
https://commons.wikimedia.org/w/api.php
  ?action=query&format=json&formatversion=2
  &prop=imageinfo&iiprop=url|size|sha1|mime
  &iiurlwidth=1920&iiurlparam=page12-1920px
  &titles=File:<檔名>.pdf
```

實測（2026-09）要注意三件事：

- Commons 只渲染**標準寬度檔位**（… 500、960、1280、1920、3840）；其他寬度直接
  請求會得到 HTTP 400，經 API 則被對齊到鄰近檔位。
- API 回傳的 `thumburl` 會被壓到「不超過 PDF 標稱頁寬的最大檔位」。國圖掃描的
  標稱頁寬約 1637 px，所以 `thumburl` 永遠是 `page12-1280px-…`，即使回應裡的
  `thumbwidth` 寫的是所要的寬度。把 URL 中的 `pageN-1280px-` 改寫成
  `pageN-1920px-` 再請求，縮圖伺服器會照給。比對過 1920 與 3840 的同區域裁切，
  細節相同，故取 **1920**（略高於原生解析度，不損失資訊）。
- `Special:Redirect/file/<檔名>?page=N&width=W` 對多頁 PDF **不認 `page` 參數**，
  一律回第 1 頁，不可用。

`scripts/fetch_pages.py` 依此實作，並：

- 依 Commons 的機器人政策帶自訂 `User-Agent`，請求間隔 ≥ 1 秒，失敗指數退避；
- 快取到 `sources/cache/<edition>/<file-slug>/p0012-w1920.jpg`，以
  `sources/cache/index.json` 記錄每頁的來源 URL、原檔 sha1、所要寬度與實際得到的
  寬高、下載時間；
- 支援 `--dry-run` 只列 URL，`--info` 只查頁數。

國圖藏陳本（見 manifest）在 1920 px 的跨頁圖上，每欄寬約 90 px，行首正篆約
70×85 px。這低於 potrace 理想的 100 px，所以第 5 節「先放大再二值化」是必要步驟
而非優化。掃描帶有國圖的淺色浮水印線條橫貫版心，二值化時要確保它落在門檻之下。

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
        render_width: 1920              # 必須是 Commons 標準檔位
        image_kind: spread              # spread（一圖兩半葉）或 half_leaf（一圖一半葉）
        pages:
          - first: 15                   # 1-based PDF 頁碼
            last: 31
            first_side: left            # 本卷自首頁的左半開始（預設 right）
            last_side: right            # 本卷止於末頁的右半（預設 left）
            juan: 卷一上
            radicals: [1, 10]           # kSEAL_Rad 編號範圍，用來推得期望篆字數
          - first: 32
            last: 58
            last_side: right
            juan: 卷一下
            radicals: [11, 14]
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
7. 兩個實測過的陷阱：國圖浮水印的橫向邊緣會被當成版框下緣（只取強度達最強者
   45% 以上的橫線，並檢查每個半葉的版框高度是否偏離中位數 4% 以上，偏離即報錯）；
   版框上緣線因紙面彎曲會在某些欄下垂，須逐欄把「填滿整欄寬的列」以上清空。

每頁輸出 `build/pages/<edition>/<slug>/p0012.json`：旋轉角、半葉與欄的座標，供後續
階段與審核工具使用。

## 3. 篆字偵測（`segment_pages.py` 第二段）

陳本一欄的結構是：頂端一個正篆，其下為單行大字楷書說解，反切與校語為雙行小字。
說解較長時佔用後續各欄（這些接續欄頂端沒有篆字）。重文篆字**行內**出現在說解
之中，後面跟著「古文某」「籒文」「或从某」等說明。要抓的是：

- **行首正篆**：欄頂第一個字格。注意接續欄頂端是楷書，不能假設每欄頂都是篆字，
  所以行首也要過分類器。
- **行內重文**：與大字楷書**同格、同大**（實測皆約佔滿欄寬），不能靠尺寸區分，
  需要一個「篆 vs 楷」二分類器。先把每欄依水平投影切成字格，再逐格分類。

分類器策略：

1. 先用行首正篆當正樣本、明顯的小字楷書當負樣本，自動得到數千筆標註。
2. 特徵用 HOG + 筆畫寬度統計（篆書筆畫等寬、轉折圓、無楷書的頓挫與尖角），
   先跑 scikit-learn 的邏輯迴歸或 SVM；不夠再上小型 CNN。
3. 對每欄的每個大字字格過分類器；雙行小字區依寬度先行排除。
4. 分類器輸出分數進 provenance，低信心的進人工審核。

每個偵測到的篆字輸出：頁、半葉、欄、閱讀順序序號、外接框、種類（headword/inline）、
分類分數。**每頁篆字數量**寫進頁面 JSON，供第 4 節核對。

## 4. 對位（`align_sequence.py`）

單純計數（第 N 個切片 = 流水號 N）只要有一個誤判或漏判，後面就全部錯位。對位要能
把這種錯誤侷限在原地。

**陳本沒有楷書字頭。** 大徐本的正篆之下直接是說解（「大也从一不聲」），並不像
段注本那樣先出一個楷書字頭，所以「OCR 字頭對 `kSEAL_MCJK`」在陳本不成立。

**以碼表字形做形狀比對。** Unicode 18.0 的篆書碼表
（`U180-3D000.pdf`）替每個碼位印出 DYC、QJZ、CCZ、THX 四個來源各自的代表字形，
下方標流水號。`scripts/chart_reference.py` 把指定版本那一欄的字形渲染成 32×48 的
比對用小圖，存在 `sources/cache/unicode/`（不提交）。這些圖**只用來核對碼位，
絕不作為描繪來源**，輪廓仍然只出自掃描切片，符合 `AGENTS.md` 的來源規則。

1. 每卷的偵測序列與期望序列（`SealSources.txt` 依流水號排序）做 Needleman–Wunsch
   對齊；配對得分＝切片與參考圖的相關係數（±2 px 位移內取最大）減去門檻 0.5，
   gap 為 −0.05，所以相似度低於 0.4 的配對不如斷開。誤判框找不到相似字形、漏判的
   字沒有切片，兩者都變成 gap，不會讓後文錯位。
2. **重新貼合**：相似度低於 0.8 的配對，在上下 0.6 格內滑動並伸縮切片框，取與參考
   圖最相似的位置（與小字相連的重文常偏移半個字，如卷一上「壻」）。**主動搜尋**：
   沒有切片的字，在前後已對位字之間的各欄裡用參考圖搜尋，相似度 ≥ 0.75 就補入。
   碼表在這兩步都只決定「截哪裡」，輪廓仍出自掃描。
3. 狀態寫入 `data/provenance/glyphs.csv`：`aligned`（頂格正篆相似度 ≥ 0.65、行內
   ≥ 0.78；行內沒有版式佐證，疊在一起的楷字可能混到 0.7）、`inferred`（依位置配上
   但相似度不足，**不進字型**，等人工裁決）、`manual`、`rejected`。例外：頂格正篆
   相似度 ≥ 0.5 且前後鄰字都已 `aligned`／`manual` 時，位置本身即是佐證，直接升為
   `aligned`（筆畫繁密的字常只有 0.6 上下）。配到低相似度切片的字，也會像漏切的
   字一樣在鄰字之間搜尋真身（門檻 0.85），找到就取代原切片。仍沒找到的字
   列在 `build/alignment/<edition>-missing.json` 與報告裡。
4. **機器拿不準的交給人。** `data/corrections.csv`：
   `action,edition,commons_title,page,side,x,y,w,h,codepoint,reason`，座標是去斜後
   半葉座標（校樣頁每格下方印的就是）。`reject` 剔除框、`add` 補入漏切的字（可附
   碼位）、`assign` 把某框釘到某碼位、`keep-lines` 指明某碼位自身有貫穿切片的筆畫
   （如「𨌥」的中豎），向量化時不可當界欄線移除。對位時套用，重跑不會遺失。
   校樣頁首只列需要決定的項目，每個候選框下方附一行可直接貼進修正檔的文字。
5. 沒有碼表參考圖時退回「逐部計數」：每部末有計數欄（「文五　重一」），
   `kSEAL_Rad` 給每部期望字數；計數吻合的部依序給碼位，但只標 `inferred`，因為
   一個誤判加一個漏判會互相抵銷（卷一示部實際發生過：雙行小字「等曰當从」被當成
   篆字，「禮」之後整段錯一格，而部計數仍是 80）。

驗收：每卷 `missing` 與 `inferred` 經人工處理後清零。

## 5. 向量化與正規化（`trace_glyphs.py`）

1. 依外接框裁切原灰階圖，外擴 8 px，放大到高 600 px（雙三次），再二值化一次。
   放大後再二值化比直接對低解析度二值圖描邊平滑得多。
2. 去噪：面積 < (筆畫寬)² × 0.3 的元件視為木刻毛邊或紙紋，刪除；但要保留篆書的
   短點畫，所以門檻用該字的筆畫寬估計值（距離變換的中位數）而非固定像素。
3. potrace：`turdsize` 依上一步門檻換算，`alphamax 1.0`，`opttolerance 0.2`。
   用純 Python 的 `potracer`（注意它描的是陣列中為 False 的像素）；輸出 SVG path，
   座標取整數字型單位以控制檔案大小。
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

- 工具：fontTools（`FontBuilder`），純 Python，不依賴 FontForge。
- 流程：讀 `glyphs/`（套用 overrides）→ `fontTools.svgLib.path.parse_path` 取得
  三次曲線輪廓 → 直接產 OTF（CFF）與 TTF（cu2qu 轉二次）。字形集不完整時照樣
  建置並回報覆蓋率；發行時加 `--require-complete`。
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

- 陳昌治本已選定國圖藏本（`NLC892-312002098744-*`，見 manifest）；藤花榭本、
  段注本、QJZ 本在 Commons 上使用哪一份掃描尚未選定。
- 腳本授權（傾向 MIT）與 OFL Reserved Font Name。
- OCR 引擎（tesseract 最省事；PaddleOCR 對罕用字辨識率通常較高但依賴重）。
