# 開源小篆 Kaiyuan Small Seal Font

一套以《說文解字》公版影印本為底、覆蓋 Unicode 18.0 篆書區塊（Seal，U+3D000..U+3FC3F，共 11,328 字）的開源小篆字型。字形由古籍掃描切字、向量化而來，每個字形都記錄出處；字型以 SIL Open Font License 1.1 發行。

An open-source Small Seal Script font covering the Unicode 18.0 Seal block
(U+3D000..U+3FC3F, 11,328 characters). Glyphs are traced from public-domain
woodblock editions of the 說文解字, every glyph carries its provenance, and
the fonts are released under the SIL Open Font License 1.1. See the English
summary at the end.

> 專案處於起步階段：目前只有對照資料與驗證腳本，尚未產出字型。

## 目標

- **完整覆蓋** Unicode 18.0 篆書區塊的 11,328 個碼位，正篆與重文皆有字形。
- **有據可查**：每個字形都能追溯到某一版本、某一頁、某一欄的掃描切片，並記錄在 `data/provenance/`。
- **可重跑**：從掃描到字型的整條管線都是腳本，人工修正以覆蓋檔的形式保存，重跑不會蓋掉人工成果。
- **開放授權**：字型採 OFL 1.1；只使用公版底本，不參考任何有授權限制的現代篆書字型或 Unicode 碼表圖。

## 對照資料與底本

碼位對照來自 Unicode 字元資料庫的 `SealSources.txt`（UAX #60），已 vendor 於 `third_party/unicode/ucd/`。它替每個碼位提供：

- 四個《說文》版本中的**流水號**（`kSEAL_THXSrc`、`kSEAL_CCZSrc`、`kSEAL_QJZSrc`、`kSEAL_DYCSrc`）。這些是各版本裡篆字依閱讀順序的序號，不是頁碼。只要把某版本的篆字按順序切出，第 N 個切片就對應該版本的第 N 號，再查碼位。
- 現代楷書對應字 `kSEAL_MCJK`，用來以 OCR 交叉核對切字是否錯位。
- 所屬部首 `kSEAL_Rad`，用於校樣分頁與檢查。

底本一律取 Wikimedia Commons 上的公版掃描，以**檔名加頁碼**定位，管線只下載需要的頁面縮圖，不把掃描檔提交進倉庫。首選陳昌治本（同治十二年，一篆一行，版面規整），陳本未收的字依 `SealSources.txt` 的屬性回退到其他版本。實際使用的檔名、頁碼範圍與版本資訊記錄在 `sources/manifest.yaml`。

## 管線概要

| 階段 | 腳本（規劃） | 產出 |
| --- | --- | --- |
| 取頁 | `scripts/fetch_pages.py` | 依 manifest 下載 Commons 頁面縮圖到 `sources/cache/`（不提交） |
| 切字 | `scripts/segment_pages.py` | 每頁的篆字裁切框與閱讀順序 |
| 對位 | `scripts/align_sequence.py` | 切片 → 流水號 → 碼位；以楷書字頭 OCR 交叉核對，異常進審核清單 |
| 向量化 | `scripts/trace_glyphs.py` | `glyphs/uXXXXX.svg`，統一 1000 UPM、置中 |
| 建字型 | `scripts/build_font.py` | `build/` 下的 TTF／OTF，cmap format 12 |
| 校樣 | `scripts/proof_sheets.py` | 依部首分頁的 HTML／PDF 校樣：篆字、原切片、楷書並排 |
| 驗證 | `scripts/verify_seal_sources.py`（已有） | 檢查 vendored 資料完整性 |

人工修正放在 `data/overrides/`（逐字 SVG）與 `data/corrections.csv`（裁切框或對位修正），建置時覆蓋自動結果。

完整設計、資料格式與驗收標準見 [`docs/technical-roadmap.md`](docs/technical-roadmap.md)。要跑管線，先建立虛擬環境並安裝 `requirements.txt`，再依 `sources/manifest.yaml` 填入 Commons 檔名與卷頁，執行 `scripts/fetch_pages.py`。

## 發行物

1. **Kaiyuan Small Seal**：字形掛在 Unicode 篆書區塊碼位上，是主字型。搭配 [OpenCC](https://github.com/BYVoid/OpenCC) 的 `t2seal` 配置可把繁體文字轉成篆書碼位後顯示。
2. **相容版**：同一套字形掛在現代漢字碼位上，供尚未支援 Unicode 18.0 的環境使用。一個漢字對應多個篆字時，取捨規則與 OpenCC `SealCharactersRev` 的正篆優先、`@reverse-prefer` 例外一致。

## 授權

- 字型：SIL Open Font License 1.1（字型產出後隨附 `OFL.txt`）。
- 腳本與工具：待定，傾向 MIT。
- `third_party/unicode/ucd/`：Unicode License v3，見該目錄的 `LICENSE.txt` 與 `README.md`；發行包須附 `THIRD_PARTY_NOTICES.md`。
- 底本為公版古籍的忠實影印，各機構的使用條款記錄於 `sources/manifest.yaml`。

## 參與

歡迎透過 issue 回報字形錯誤（請附碼位與截圖）、透過 PR 提供 `data/overrides/` 修正，或協助審核校樣。開發約定見 `AGENTS.md`。

## English summary

Kaiyuan Small Seal (開源小篆, "open-source small seal") aims to give every
code point of the Unicode 18.0 Seal block a glyph traced from public-domain
scans of the 說文解字, with per-glyph provenance. The Unicode `SealSources.txt`
file supplies, for each code point, its running sequence number in four
editions of the book; cutting the seal headwords of a scanned edition in
reading order therefore aligns them with code points directly, and the
modern-equivalent property lets OCR of the regular-script headword catch
misalignments. Scans come from Wikimedia Commons and are addressed by file
name and page number; only page thumbnails are fetched and nothing scanned is
committed. The pipeline (fetch, segment, align, trace, build, proof) is fully
scripted, with human corrections kept as override files. Fonts will be
released under the SIL OFL 1.1; the vendored Unicode data is under the
Unicode License v3. The project is at an early stage: the mapping data and a
verification script exist, the fonts do not yet.

## 开发状态

- [x] 项目初始化、文档与技术路线
- [ ] Manifest 填入 Wikimedia Commons 文件名  
- [ ] 第一份扫描成功切字和对位
- [ ] 字型端到端build成功
- [ ] v0.1 发布

## 相關項目與既有成果

本專案並非孤立開發。以下是業界已知的小篆字體與相關工作：

### 現有開源/免費篆字項目

| 項目 | 字數 | 授權 | 特點 | 與本項目的關係 |
| --- | --- | --- | --- | --- |
| **崇羲篆體** | 11,596 字 | CC BY-ND 3.0 TW | 基於《說文解字》與教育部常用字表；禁止改作 | 參考用，不作為繪製源；字數略少於 Unicode 18.0 Seal |
| **全字庫說文解字體** | 全字庫編碼 | OFL 1.1 / 政府資料開放 | 掛在 CJK 碼位上；政府製作，質量穩定 | 參考用；字型掛位不同（不在篆書區塊）；可作相容版後備 |
| **Unicode 碼表圖** | 11,328 字 | ？（提案方專有） | UAX #60 圖示用 | **不可參考**（授權限制，且為示意非正式字形） |

### 本項目的獨特性

- **碼位涵蓋**：Unicode 18.0 Seal 區塊（U+3D000..U+3FC3F）**全 11,328 字** ← 比崇羲篆體多
- **來源可追溯**：每個字形都標註版本、Commons 檔案、頁碼、裁切框 ← 現有項目未公開
- **正篆與重文**：同時收錄正篆與重文形態 ← 崇羲篆體採單形式
- **公版底本**：從古籍掃描追跡，**非參考現代篆字字型** ← 確保獨立著作權
- **機械化管線**：完全可重跑，人工修正以 override 形式保存 ← 便於社群貢獻與版本迭代

### 致謝與資料來源

- **Unicode 字元資料庫**：`SealSources.txt` (UAX #60) 提供的碼位對應
- **Wikimedia Commons**：[小篆字體相關頁面](https://en.wikipedia.org/wiki/Small_seal_script)
- **開源字型社群**：參考 [OSFCC](https://github.com/DrXie/OSFCC) 與 [justfont blog](https://blog.justfont.com/) 的相關文章
- **崇羲篆體項目**：作為品質參考與社群先例
