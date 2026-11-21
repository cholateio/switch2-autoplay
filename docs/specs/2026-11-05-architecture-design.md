# switch2-autoplay 設計文件（2026-11-05）

> 本文件取代 `docs/PROJECT_BRIEF.md` 成為需求與架構的權威來源。PROJECT_BRIEF.md 是早期腦力激盪，保留作歷史參考。
> 研究事實見 `docs/research/`。標「**我的決定**」的是 Claude 依討論做的判斷，user 可推翻；標「**user 決定**」的是 user 明說的。

## 1. 意圖

做一台「給模型眼睛和手，讓它自己玩 Switch 2」的機器。眼睛是擷取卡畫面，手是假扮成有線手把的 ESP32-S3。
專案的本體是**遊戲無關的機械層**（截圖 → 判斷 → 按鍵 → 確認），遊戲知識以可抽換的 game pack 掛上去。
第一個 game pack 是《Pokémon Pokopia》，最終示範：給 AI 一段建築短片（例如 `Pokopia-5x5-Larvitar` 這支 52 秒直式影片），
它拆解結構、上網查配方與素材、在遊戲裡蒐集材料、在空地上蓋出一個「像」的建築。不要求一模一樣，有自己風格更好。

**為什麼這樣切**（user 決定）：使用情境是「玩遊戲」，不是寫程式或逛網頁，核心迴路值得高度客製；
而遊戲會換，底層做一次就要能重用。若 Pokopia 窒礙難行，可換成更簡單的 2D 或文字冒險遊戲，底層不變。

## 2. 目標 / 非目標 / 反目標

**目標**
- G1 底層 core 完全遊戲無關：換遊戲只新增一個 game pack 資料夾，core 不改。
- G2 人幾乎不介入：模型卡住自己重試、自己從主選單恢復；不要頻繁通知人。
- G3 幀率優先於畫質；畫面不經壓縮與網路。
- G4 離線可開發：影像可換成錄影或截圖資料夾，手把可換成 dummy，沒接主機也能跑測試。
- G5 每個動作「做 → 確認」，不寫盲目的長巨集。
- G6 最終 agent 迴圈自養（Anthropic SDK），不依賴 Claude Code 當宿主。

**非目標（這次不做）**
- 不做連線功能自動化（線上多人、Cloud Island、交易）。
- 不改機、不讀寫遊戲記憶體、不碰 sys-botbase。
- 不做即時反應類遊戲（槍戰、動作、類魂）。延遲預算以「秒」計。
- 不做陀螺儀、震動、NFC。
- 不做 MQTT／WebRTC 遠端監看（之後可加，與 core 解耦）。

**反目標（做到了也算失敗）**
- 底層混進 Pokopia 的知識（按鍵對照、模板、座標）。
- 為了省事寫出「按 A 十次再等 3 秒」這種不看畫面的巨集。
- 電腦當掉時角色一直往前跑（韌體看門狗失效）。
- 模型每一幀都被呼叫，一小時燒掉幾十美元。

## 3. 限制

**硬體**（已採購）：Switch 2 + 底座；AVerMedia GC551 擷取卡（USB，M0 實測確認模式與 fps）；
ESP32-S3-WROOM-1 N16R8 雙 Type-C 副廠板（串口晶片 CH343P）；另有 TC358743 CSI-2 擷取板，本期不用。
電腦：Windows 11，Intel Core Ultra 7 265KF，32 GB，RTX 5060 Ti 16 GB。

**Switch 2 時序**（來源：PokemonAutomation 實測，見 PROJECT_BRIEF §3）
- 有線手把輪詢率在 125 Hz 與 62.5 Hz 間浮動 → 任何手把狀態至少維持 16 ms。
- 按鍵至少 40 ms 才被認成一次按下；預設 50–80 ms。
- 系統選單捲動間隔 48 ms。
- 「同時按、重疊按」技巧不可靠，不依賴。
- 主機設定：關 HDR、色彩正常、文字一般、開「Pro 控制器有線通訊」、使用帳號放最左。
- 多數遊戲同時只吃一支手把：ESP32 與實體手把輪流用。

**規則**：只自動化單機內容；成果不販售、不參加排名。

## 4. 架構

```
┌──────────────────────────── agent（慢迴路，秒級）────────────────────────────┐
│ Claude API 迴圈：截圖 + 狀態摘要 + 記憶 → 決定下一步 → 呼叫工具           │
│ 工具 = core 工具（看、按、等）+ 當前 game pack 的工具                       │
└──────────────┬───────────────────────────────────────────┬───────────────────┘
               │                                           │
┌──────────────▼───────────── core（快迴路，毫秒級）────────▼───────────────────┐
│ perception: 最新幀、ROI、模板比對、顏色、OCR、幀差異                         │
│ actions:    press / hold / stick / wait_until(predicate) / 重試 / 逾時       │
│ runtime:    看門狗、緊急停止、log、失敗存前後幀                               │
│ capture:    GC551 / 影片檔 / 截圖資料夾（同介面）                            │
│ controller: serial（自製韌體）/ dummy（同介面）                              │
└──────────────┬─────────────────────────────────────────────────────────────────┘
               │ USB-serial（CH343P）
┌──────────────▼───────────── firmware（ESP32-S3，ESP-IDF C）─────────────────┐
│ 收「狀態 + hold_ms」封包 → 佇列 → 以 USB HID 回報給 Switch；300 ms 無心跳全放開 │
└─────────────────────────────────────────────────────────────────────────────┘

game_packs/<game>/   按鍵對照、狀態模板、專用工具、給模型的玩法說明、manifest
```

### 4.1 firmware（**我的決定**，user 已同意路線 B）

- ESP-IDF + TinyUSB，C 語言。OTG 口接 Switch 2 底座，CH343P 串口接電腦。
- **USB 描述符先用 HORI Pokken Pad（0F0D:0092）**，描述符與 report 封裝做成可抽換模組。
  若 Switch 2 不認（研究只找到間接證據，見 `docs/research/2026-11-05-controller-routes.md`），
  備案依序：(1) `espp/switch_pro` 元件模擬 Switch 1 Pro（057E:2009，需開「Pro 控制器有線通訊」）；
  (2) `espp/switch2_pro` BLE 模擬 Switch 2 Pro（已在真 Switch 2 驗證，但元件只有一週大）。
- 序列協定（**我的決定**，細節留給 builder）：固定 magic + 長度 + 類型 + payload + CRC-8。
  指令：`SET_STATE{buttons u16, hat u8, lx, ly, rx, ry u8, hold_ms u16, seq u8}`、`RELEASE_ALL`、`PING`。
  回覆：`ACK{seq}`（收到）、`DONE{seq}`（維持時間結束）。裝置端佇列深度 8；佇列滿回 `BUSY`。
  鮑率 921600。**時序由韌體保證**：每個狀態至少維持 max(hold_ms, 16 ms)。
- 看門狗：300 ms 沒收到任何封包（含 PING）→ 全部放開、搖桿置中。這是 M1 驗收項，拔線實測。
- 先用 PokemonAutomation 的 PABotBase2 `.bin` 做 M0 硬體驗證（個人非商業使用），之後不再依賴它。

### 4.2 core（Python 3.11+，Windows 原生，uv 管理）

**user 決定**：Python。理由：視覺、OCR、SDK 生態；延遲預算寬鬆；精準時序已在韌體。

- `capture/`：三種來源同一介面 `FrameSource.latest() -> Frame(bgr, ts, seq)`。
  GC551 走 PyAV（ffmpeg 的 DirectShow 輸入）以非阻塞方式讀取，**未壓縮 YUY2，1080p60**；背景執行緒只保留最新一幀，不累積延遲。
  OpenCV 只負責影像處理，不負責讀擷取卡（2026-11-17 M0 實測決定，原因見 `docs/DEVELOPMENT.md` 的 Hardware facts）。
  顯示實際 fps、擷取延遲、全黑偵測；`s` 鍵存全解析度截圖。（重新設計，原 capture.py 已遺失。）
- `perception/`：所有座標與模板以 **1920×1080 為基準**，實際解析度不同時自動換算。
  提供 `match_template`、`roi_color`、`frame_diff`、`ocr(roi)`（繁中，RapidOCR/PaddleOCR，GPU）。
  **狀態偵測器**是 game pack 註冊進來的 callable，core 只提供執行與信心值的框架。
- `controller/`：`Controller` 介面：`press(button, ms)`、`hold(button)`／`release`、`stick(which, x, y, ms)`、`release_all()`、`heartbeat()`。
  後端 `serial`（4.1 協定）與 `dummy`（只印 log）。按鍵名稱是 Switch 通用名（A/B/X/Y/L/R/ZL/ZR/PLUS/MINUS/HOME/CAPTURE/LS/RS/DPAD_*），
  不是遊戲語意。
- `actions/`：**做 → 確認**原子操作：`do_until(action, predicate, timeout, retries)`，失敗時存前後 N 幀與原因。
  時序常數（按壓時間、選單間隔、最短維持）集中在 `config/timing.yaml`。
- `runtime/`：緊急停止（全域快捷鍵 F12 → `release_all` 並停止）；每次執行一個 log 目錄；看門狗心跳執行緒。
- `tools/`：`press A`、`hold LSTICK_UP 500ms` 這類 CLI；模板擷取與 ROI 框選工具；延遲量測工具（按下到畫面變化）。

### 4.3 agent（Anthropic SDK，Python）

- **MVP 階段先自養一個最薄的迴圈**（**我的決定**，取代「先用 MCP + Claude Code」的提議）：
  用 Anthropic Python SDK 的 tool runner，工具函式就是 core 與 pack 暴露的 Python 函式，不另外包 MCP。
  理由：tool runner 本身就是迴圈，寫 MCP 反而多一層；user 最終要自養迴圈，MVP 直接走同一條路不浪費。
  需要時再用一個薄 adapter 把同一組工具暴露成 MCP server。
- 模型：預設 `claude-opus-5-5`（SDK skill 的預設），可在設定檔切換；思考用 adaptive，`effort` 可調。
- 迴圈：`截圖（降到 1280×720 JPEG）+ 快迴路狀態摘要 + 記憶摘要 → 模型 → 工具呼叫 → 執行 → 回報結果`。
  **只在畫面有變化或動作完成時呼叫模型**，不是每幀都問。
- 記憶：一份執行中的 notes（markdown），模型用工具讀寫，跨回合保留計畫與進度；對話歷史定期壓縮。
- 參考實作（皆 GitHub，2026-11-05 查）：`lmgame-org/GamingAgent`（1004★，ICLR 2026，perception/memory/reasoning 模組 + 每遊戲一個 env 資料夾，
  與本專案的 pack 概念對應）、`davidhershey/ClaudePlaysPokemonStarter`（206★，最小 Claude 迴圈範本）、`alexzhang13/videogamebench`（378★）。
  借鏡結構，不直接用（它們接模擬器，不接實機）。

### 4.4 game pack 介面（**我的決定**，細節留給 builder）

```
game_packs/pokopia/
├── manifest.yaml        名稱、語言、顯示解析度假設、按鍵語意對照（"互動"→A）、提供的工具與狀態偵測器清單
├── prompt.md            給模型的玩法說明：目標、操作要點、禁止事項
├── states/              狀態偵測器（Python）＋模板圖（1080p 基準）
├── tools/               專用工具（Python 函式，含 docstring 作為 tool description）
└── knowledge/           配方資料、網站清單、模型可查的筆記
```

- core 透過 manifest 載入 pack；pack 只能呼叫 core 的公開介面。
- Pokopia pack 第一批工具（M5 用）：`analyze_build_video(path)`（抽幀 → 模型 → 結構清單）、`lookup_recipe(item)`
  （查 thegameswiki／Serebii）、`walk_to(target)`、`face(direction)`、`place_block()`、`select_move(name)`。
  不是本期全部要做完，見里程碑。

## 5. 里程碑與驗收

每個里程碑完成後停下來讓 user 實機測試，再進下一個。

| 里程碑 | 內容 | 驗收 |
|---|---|---|
| **M0 硬體驗證** | 刷 PABotBase2 .bin，用 PA 官方程式鍵盤控制；寫新的 capture 工具 | PA 程式能在 Switch 2 主選單移動游標；capture 工具穩定顯示畫面並記錄實際解析度、格式、fps、延遲；存下 Pokopia 關鍵畫面各 ≥3 張（主選單、遊戲中、對話框、對話選項、黑畫面讀取、招式選單、合成台） |
| **M1 韌體與手把** | 自製韌體（USB Pokken，可抽換）+ `Controller` serial/dummy 後端 + CLI | 主選單用程式移動游標並開啟設定，重複 100 次不漏按、不卡鍵；拔電腦端 USB，角色不會一直跑；`press`/`hold` CLI 可用 |
| **M2 擷取與感知** | capture 三來源、perception 基礎、模板工具、`do_until` | 用 ≥10 張截圖離線測試，正確判斷主選單／遊戲中／黑畫面；`do_until` 在 dummy + 錄影下可跑測試 |
| **M3 第一個閉迴路（pack 雛形）** | agent 最薄迴圈 + 攻略載入 + 三層記憶 + Pokopia 操作情境偵測（探索／背包／主選單／對話／擺放）。2026-11-21 改範圍，見第 9 節 | 模型自己開背包讀出內容再關掉、開主選單再回來，各連續 10 次都回到探索情境；遇到對話框能推進；全程不送出危險鍵（背包的 `＋`、主選單的 `－`）；模型呼叫次數與費用有紀錄 |
| **M4 移動與放置原語** | 校準「推搖桿 N ms 走多遠」、鏡頭重置、`walk_to`/`face`/`place_block` 閉環；以電磁飄浮為主要建造方式，PP 不足時改用一般放置（見第 9 節） | 在空地放出一排 5 格方塊並用畫面確認每格；失敗自動重放 |
| **M5 影片 → 建築** | `analyze_build_video`、`lookup_recipe`、材料清單、蒐集與建造流程 | 給 Larvitar 短片，在已解鎖的沙盒存檔上蓋出評審認可「像」的建築；人全程不介入（可觀看） |

M0–M2 是 core，M3 起才碰 Pokopia 知識。M5 之前若 Pokopia 證實不可行，可換遊戲，M0–M2 不變。

## 6. 留給 builder 的事

- 序列協定的確切位元配置、CRC 多項式、重送策略。
- Pokken 描述符與按鍵位元順序：**對照 `progmem/Switch-Fightstick/HORI_Descriptors` 驗證，不憑記憶寫**。
- ~~GC551 的實際開法（DirectShow vs MSMF）與 YUY2 轉 BGR 的位置~~：已決定（2026-11-17）——PyAV 的 DirectShow 輸入，YUY2 轉 BGR 在讀取執行緒內完成。
- OCR 引擎選型（RapidOCR vs PaddleOCR）與 GPU 綁定。
- notes 記憶的格式與壓縮策略。
- 測試框架與離線 fixture 的組織方式。
- 韌體開發環境：WSL2 編譯、Windows 燒錄（esptool），或全在 Windows。

## 7. builder check 後補充的決定

獨立審查員讀過本文件後提出六個問題，處理如下（標示同前）。

- **沙盒存檔**（user 決定）：專用沙盒，已解鎖招式、配方與材料，可以被玩壞。pack 不需要保守的禁止清單，
  但仍不做連線功能。
- **M4 放置原語以單格為準**（我的決定）：單格放置在任何進度都可用；Magnet Rise 3×3 若存檔已通關則作為加速選項，
  不是前提。
- **恢復範圍**（我的決定）：M3 的自動恢復只到 Pokopia 標題畫面與遊戲內選單。Switch HOME 層級的恢復
  （遊戲當掉重開、選最左帳號、跳過更新提示）做成獨立的 `game_packs/switch_system/`，排在 M5 之後。
- **參考影片**（我的決定）：`Pokopia-5x5-Larvitar-SnapYT.App.mp4`（360×640、30 fps、52 s、H.264）是他人作品，
  **不進 repo**（repo 公開）。放在 gitignore 的 `fixtures/` 供 `analyze_build_video` 離線測試；M5 評審由 user 目視認定「像」。
- **模型費用**（我的決定）：agent 內建每次執行的 token／美元上限（設定檔），超過即安全停止並存狀態；
  例行判斷與規劃可用不同模型，預設皆 `claude-opus-5-5`，切換只改設定。
- **開發迴圈**（user 決定）：Claude Code 在 WSL2 工作，透過 interop 呼叫 Windows 原生 Python 跑實機測試，
  可直接操作擷取卡、COM 埠與 Switch。**初期實機執行時 user 要能看到畫面**：GC551 的 HDMI 直通接電視或螢幕
  （M0 確認背板有 HDMI OUT），capture 工具另開預覽視窗；不用 OBS，GC551 一次只能被一個程式開啟。
- **空地**（user 決定）：user 手動清好一塊 10×10 平地，M4/M5 在那裡進行。
- **API 帳號與預算**（查證 2026-11-05，來源 platform.claude.com `api-credits-for-subscribers`、code.claude.com `agent-sdk/quickstart`）：
  Messages API 與 Agent SDK 都走 Console 計費（API key），claude.ai 訂閱額度不涵蓋；Max 方案每月附 US$100 Console API 額度
  可用於 Agent SDK 與 Claude API（不含 Claude Code），Pro 方案沒有。user 每月上限 US$50，agent 以設定檔熔斷。
  key 放 gitignore 的 `.env`，由 Claude 建範本後用記事本開給 user 填。user 為 Max 以上方案，附帶額度足夠 MVP。
- **本機 GPU 的角色**（我的決定）：RTX 5060 Ti 16 GB 在 MVP 只跑快迴路的 OCR 與模板比對。慢迴路的模型後端做成
  可抽換介面（`anthropic` / `local`），但**本機 VLM 的評估延到 M3 之後**：M3 會記錄每次模型呼叫的類型、token 與延遲，
  有了資料再決定哪些高頻簡單判斷（狀態分類、讀對話框）值得改交 7–8B 級本機 VLM。MVP 期間額度不是瓶頸，不先花時間架本機模型。

## 8. 已知風險

- Pokken 描述符在 Switch 2 可用性只有間接證據；M1 第一天就實插驗證，不通立刻換備案。
- Pokopia 沒有藍圖功能，建造是幾百次單格放置的閉環；放置位置受鏡頭俯仰與面向影響。M4 是整個專案最難的里程碑。
- 日夜與天氣改變畫面：模板要在多個時段取樣，或多依賴 OCR 與模型而非顏色。
- 遊戲更新可能改 UI：模板與 ROI 集中管理，換版本只動 pack。
- 模型費用：以 Opus 5.5 估，若每 5 秒一次 1500 token 輸入約每小時 US$4；靠「只在變化時呼叫」與快取壓低。費用必須記 log。

## 9. M3 開始前的決定（2026-11-21）

M2 驗收後與 user 討論的結果（標示同前）。與前文衝突時以本節為準。

- **M3 改範圍**（user 決定）：user 的存檔接近通關，對話很少、功能大多已解鎖，「連續 20 段對話」湊不出來。
  M3 的主角改成「認出操作情境並在情境之間安全來回」，對話只是其中一種情境。
- **遊戲知識做成「攻略」，結構參考 skill**（user 決定方向，分層是我的決定）：分層載入，不必與 Claude 的 SKILL.md 格式相容。
  - 核心指南：玩法概要、按鍵對照、情境判斷表、禁止事項；每次呼叫模型都帶。
  - 主題章節：一個主題一個檔案（招式、擺放、背包、主選單…）；模型用工具按需讀。
  - 資料：配方這類要查的表，用查詢工具，不整份載入。
- **攻略來源**（user 決定）：網路資源為主（攻略站、user 提供的 YouTube 說明影片），不靠 user 口述。
  第一份底稿是 user 整理的 `game_packs/pokopia/knowledge/controls-manual.md`。影片與字幕原文只放 gitignore 的
  `fixtures/`；repo 裡只有自己寫的摘要與來源連結。每一章實際使用前要在實機上驗證。
- **記憶分三層**（我的決定，借 Hermes Agent 的分法，不採用 Hermes 或外部記憶服務）：
  - 進度筆記：現在在哪、身上有什麼、目前的計畫；有字數上限，滿了先整併；每次呼叫都帶。
  - 經驗筆記：模型自己寫的教訓（何時用、步驟、常見失敗、如何驗證）。先進草稿區，
    之後實際用到且成功才升級進攻略章節；升級要經 user 或驗證流程。
  - 歷史：現有的 RunLog；需要時再加搜尋。
  記憶全部是本機檔案。量大到檔案搜尋不夠用時才考慮向量檢索。
- **危險鍵由程式擋**（我的決定）：背包情境的 `＋`（丟棄）與主選單情境的 `－`（存檔並回標題）不靠模型自律，
  pack 宣告「情境 → 禁用鍵」，執行層在送出前檢查。
- **建造方式**（user 決定）：以電磁飄浮（Magnet Rise，破關後解鎖）為主，取代第 7 節「單格為準、Magnet Rise 只是加速選項」。
  電磁飄浮會持續耗 PP，PP 不足時改用一般放置，所以兩種放置都要做。電磁飄浮的操作目前沒有文字來源，
  M4 開始前要有 user 示範或說明影片。
- **模板圖不進 repo**（我的決定）：模板是遊戲截圖的裁切，repo 公開。pack 的狀態定義檔進 repo，
  模板圖留在 gitignore 的位置，缺圖時載入器要說清楚缺哪一張。
