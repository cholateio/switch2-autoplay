# 自製部分的參考專案查證（2026-11-10／11，research-scout 查證，主對話整理）

只保留查證到的事實與來源。星數與最後 push 取自 `api.github.com`（查證當日）。
篩選門檻：知名組織或維護者／約 500★ 以上／一年內有更新／授權清楚，四項至少符合兩項。

## 序列封包與重送 → 維持自製

- `min-protocol/min`：286★，README 寫 MIT，最後 push 2024-01-30。C 與 Python 都有，T-MIN 是 sliding window 重送。
  遺失 ACK／重複投遞的行為未查證（規格頁打不開）。
- `PowerBroker2/SerialTransfer`：488★，MIT，2026-11-06。COBS + CRC8；Python 端與 ACK 行為未查證。
- `MightyPork/TinyFrame`：438★，MIT，2022-08-26。
- 三個都沒過門檻；沒有找到同時提供 C 與 Python、含重送與去重的成熟函式庫。

## 看畫面的自動化架構 → 借鏡設計，不當相依套件

- `MaaXYZ/MaaFramework`：5166★，LGPL-3.0，2026-11-10（v5.16.0）。pipeline 的 `post_wait_freezes`（等畫面穩定再驗證）、
  `timeout` → `on_error`。Python 套件 `MaaFw` 有 `CustomController`（要實作 `screencap`、`click`、`swipe`、`key_*`、`start_app` 等），
  內建手把只有 ViGEm 的 Xbox360／DS4。`rate_limit` 預設 1000 ms、`post_delay` 預設 200 ms，偏 UI 流程。
  在 Windows 註冊 CustomController 能否跑通未實測。
- `LmeSzinc/AzurLaneAutoScript`：9794★，**GPL-3.0（只借概念，不複製程式碼）**，2026-11-09。
  `ui_click` 每圈重新截圖，條件要連續成立 `confirm_wait` 秒才算成功；`appear(interval=)` 防止動作後立刻重複觸發。
- `PokemonAutomation/Arduino-Source`（SerialPrograms）：42★，MIT，2026-11-10。`CommonTools/VisualDetector.h`：
  `StaticScreenDetector::detect` 單幀偵測；`DetectorToFinder` 要結果持續一段時間才回報。
- 採用：`do_until`／`wait_until` 加 `stable_ms`（條件連續成立一段時間才算數）。

## Windows 低延遲擷取 → 維持自製

- 沒有找到可信的逐幀延遲公開數字。OpenCV 論壇（2025-10）量的是首幀延遲：MSMF 約 2.9–5.7 s、DSHOW 約 1.0–1.4 s；
  MSMF 可能預設選壓縮格式，建議明確設 `CAP_PROP_FOURCC`（計畫已這樣做）。
- `abhiTronix/vidgear`：3734★，Apache-2.0，2026-11-07。能否控制 Windows 後端未查證。

## USB HID 手把韌體 → GP2040-CE 當第二對照來源

- `OpenStickCommunity/GP2040-CE`：2595★，MIT，2026-11-06。Switch 模式用 0F0D:0092「HORI CO.,LTD.」「POKKEN CONTROLLER」。
  `headers/drivers/switch/SwitchDescriptors.h`（2026-11-11 下載）與 `progmem/Switch-Fightstick` 逐欄一致，唯一差異 bInterval 1（原廠 5）。
- Switch 2 相容性只有間接證據：PR #1790（2026-10-25 合併）寫「Switch 1 issues a clear-halt on the Pokken pad's interrupt IN endpoint」
  「Switch 2 doesn't, so it was unaffected」。該迴歸出在 TinyUSB 0.21 的 **RP2040** `dcd_edpt_clear_stall()`，與 ESP32-S3 的驅動無關。
  沒有找到「Pokken 模式在 Switch 2 可用」的直接測試報告；仍以 Task 4 的 Gate A 實測為準。
- `espressif/esp-usb`（`device/esp_tinyusb`）：78★，2026-11-10，官方元件。`espressif/esp-idf` 的 `tusb_hid` 範例是鍵盤＋滑鼠，沒有自訂手把描述符的範例。

## 來源

- https://api.github.com/repos/min-protocol/min ・ https://raw.githubusercontent.com/min-protocol/min/master/target/min.h
- https://api.github.com/repos/PowerBroker2/SerialTransfer ・ https://api.github.com/repos/MightyPork/TinyFrame
- https://api.github.com/repos/MaaXYZ/MaaFramework ・ https://raw.githubusercontent.com/MaaXYZ/MaaFramework/main/docs/en_us/3.1-PipelineProtocol.md
- https://raw.githubusercontent.com/MaaXYZ/MaaFramework/main/source/binding/Python/maa/controller.py
- https://api.github.com/repos/LmeSzinc/AzurLaneAutoScript ・ https://raw.githubusercontent.com/LmeSzinc/AzurLaneAutoScript/master/module/ui/ui.py
- https://raw.githubusercontent.com/PokemonAutomation/Arduino-Source/main/SerialPrograms/Source/CommonTools/VisualDetector.h
- https://forum.opencv.org/t/the-msmf-backend-is-slow-to-acquire-the-initial-frame/24121
- https://raw.githubusercontent.com/OpenStickCommunity/GP2040-CE/main/headers/drivers/switch/SwitchDescriptors.h
- https://github.com/OpenStickCommunity/GP2040-CE/pull/1790 ・ https://api.github.com/repos/OpenStickCommunity/GP2040-CE/issues/1785
