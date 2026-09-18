# 開源小篆 Kaiyuan Small Seal Font

一套以《說文解字》公版影印本為底、覆蓋 Unicode 18.0 篆書區塊（Seal，U+3D000..U+3FC3F，共 11,328 字）的開源小篆字型。字形由古籍掃描切字、向量化而來，每個字形都記錄出處；字型以 SIL Open Font License 1.1 發行。

An open-source Small Seal Script font covering the Unicode 18.0 Seal block
(U+3D000..U+3FC3F, 11,328 characters). Glyphs are traced from public-domain
woodblock editions of the 說文解字, every glyph carries its provenance, and
the fonts are released under the SIL Open Font License 1.1. See the English
summary at the end.

> 專案處於起步階段：管線已用陳昌治本卷一端到端跑通（切字、對位、向量化、建字型、校樣），尚未涵蓋全書，也尚未發行字型。

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

| 階段 | 腳本 | 產出 |
| --- | --- | --- |
| 取頁 | `scripts/fetch_pages.py` | 依 manifest 下載 Commons 頁面縮圖到 `sources/cache/`（不提交） |
| 切字 | `scripts/segment_pages.py` | 每頁的篆字裁切框與閱讀順序 |
| 對位 | `scripts/chart_reference.py`、`scripts/align_sequence.py` | 切片 → 流水號 → 碼位；以 Unicode 碼表中該版本的字形做形狀比對的序列對齊（碼表只用於核對，不作描繪來源），拿不準的進審核清單 |
| 向量化 | `scripts/trace_glyphs.py` | `glyphs/uXXXXX.svg`，統一 1000 UPM、置中 |
| 建字型 | `scripts/build_font.py` | `build/` 下的 TTF／OTF，cmap format 12 |
| 校樣 | `scripts/proof_sheets.py` | `build/proof/index.html`：字型渲染、原切片、楷書並排，附待審清單 |
| 驗證 | `scripts/verify_seal_sources.py`（已有） | 檢查 vendored 資料完整性 |

人工修正放在 `data/overrides/`（逐字 SVG）與 `data/corrections.csv`（每行一個決定：`reject` 剔除誤判框、`add` 補入漏切的字、`assign` 指定碼位），重跑時自動套用。校樣頁會列出機器拿不準的項目與可直接抄用的座標。

跑一遍（需能連上 Wikimedia Commons）：

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python3 scripts/fetch_pages.py --edition ccz      # 依 manifest 抓頁面到 sources/cache/
python3 scripts/segment_pages.py --edition ccz    # 切字，輸出 build/pages/
python3 scripts/chart_reference.py --edition ccz  # 從 Unicode 碼表取比對用參考圖（只存快取）
python3 scripts/align_sequence.py --edition ccz   # 對位，輸出 data/provenance/glyphs.csv
python3 scripts/trace_glyphs.py                   # 向量化，輸出 glyphs/
python3 scripts/build_font.py                     # 建字型，輸出 build/*.ttf、*.otf
python3 scripts/proof_sheets.py                   # 校樣，輸出 build/proof/index.html
```

完整設計、資料格式與驗收標準見 [`docs/technical-roadmap.md`](docs/technical-roadmap.md)。

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
- [x] Manifest 填入 Wikimedia Commons 文件名（陈昌治本十册已选定；卷一、卷二页码已核定）
- [x] 第一份扫描成功切字和对位（卷一 783 字中 732 字高信心对位，其余列入人工审核清单）
- [x] 字型端到端build成功（卷一已对位部分，TTF/OTF 主字型与相容版）
- [ ] 陈昌治本全书切字对位、审核清单清零
- [ ] v0.1 发布
