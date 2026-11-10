# 手把路線研究紀錄（2026-11-05，research-scout 查證，主對話整理）

結論與推論見 PR / 對話；本檔只保留查證到的事實與來源，供之後寫計畫時引用。

## PABotBase2（路線 A）

- `PokemonAutomation/ComputerControl` 沒有 LICENSE 檔，只是發佈／wiki repo。
- 原始碼在 `PokemonAutomation/Arduino-Source`：LICENSE 為 MIT（Alexander J. Yee, 2021）。
  README：原始碼 MIT「unless otherwise specified」；**預編譯 binary 僅限非商業使用**；
  專案有一部分未開源。
- 公開的 PABotBase2 程式碼只有協定與電腦端：
  `Common/PABotBase2/`（封包協定、ReliableConnectionLayer、Controllers）、`Common/CRC32/`、
  `Common/ControllerStates/`、`SerialPrograms/Source/Controllers/PABotBase2/`（C++/Qt 客戶端）。
  **ESP32-S3 韌體本體（TinyUSB、描述符、NS1/NS2 模擬）未找到原始碼**，只發 `.bin`。
- 協定摘要（來源：`Common/PABotBase2/ReliableConnectionLayer/PABotBase2_PacketProtocol.h` 等）：
  - 封包：`0x81 magic, u8 seq, u8 packet_bytes(含 CRC, 0=256), u8 opcode(0x80=重傳旗標)` + payload + CRC-32C（4 bytes，
    以 session ID 當 seed；ASK_RESET 用 0xFFFFFFFF）。
  - 可靠層：seq ack、每 slot 重傳計時、reorder window、PC 端 80ms timeout；command queue 有
    `CQ_COMMAND_FINISHED`/`CQ_COMMAND_DROPPED`。
  - 訊息層：`u16 message_bytes, u8 opcode, u8 id`；設定有線手把狀態 = opcode `0x90`，
    body = `u16 milliseconds` + 7 bytes `buttons0, buttons1, dpad, LX, LY, RX, RY`（中立：0/0/8/128×4）。
  - 版本號是日期碼（connection `2026061800`、message `2026061804`），連線時比對；PA 多次 release 說「需重刷韌體」。
  - 鮑率：客戶端先試 921600 再 115200；DTR=1、RTS=0 避免 ESP32 重開。
  - **沒有任何現成 Python／第三方客戶端。**
- 未查證：session ID 取得流程、stream offset 語意（在 `PABotBase2CC_ReliableStreamConnection.cpp`、`PABotBase2_DeviceHandle.cpp`）。

## Switch 2 有線手把相容性（路線 B 的前提）

- PA v0.55.1 release note：有線設備（ESP32-S3、Arduino/Teensy）「不再模擬 modified Hori Pokken Controller，
  改模擬 Horipad Turbo for Switch 2」，因而取得 GL/GR/C 鍵，且在 Switch 2 顯示 Pro 手把圖示。
  → 推論：v0.55.1 之前 PA 的 Switch 2 支援就是用 Pokken 描述符，改版動機是多出按鍵，不是 Pokken 失效。此推論未經實機驗證。
- PA Controller Guide 列出的類型：NS1 Wired Controller（14 鍵）、NS2 Wired Controller（17 鍵，舊韌體不支援，
  「Nintendo 第三方手把採白名單」）、NS1 Wired Pro Controller（需開「Pro 控制器有線通訊」）。
  ESP32-S3 開機預設為「NS1: Wired Pro Controller」（v0.67.9）。
- Horipad Turbo for Switch 2 的 VID/PID／描述符沒有公開資料；需從實機抓或從 PA .bin 擷取。
- Pokken 描述符（Switch 1 時代）：`progmem/Switch-Fightstick/HORI_Descriptors`：USB 2.00 full-speed、
  單一 HID 介面、EP1 IN / EP2 OUT interrupt 64 bytes bInterval 5、8-byte input/output report、13 鍵、hat、4 軸、1 vendor byte。

## ESP32-S3 + TinyUSB 參考實作

- `espp/switch_pro`（ESP-IDF component，MIT）：模擬 Switch 1 Pro Controller（057E:2009）含 0x80/0x81 握手、0x21 子命令、0x30 回報。
  坑：`CONFIG_TINYUSB_HID_BUFSIZE=64`；native USB 被 HID 佔用，log 走 UART0；需開「Pro 控制器有線通訊」。
- `jyapayne/switch-pico`（MIT，Pico）：Pro Controller 協定，UART 921600 平面串流 + Python 橋接。
- `radiantwf/esp32-circuitpython-switch-joystick`：VID 0F0D / PID 00C1（HORIPAD S），Switch 1。
- `espp/switch2_pro`（MIT）：BLE 模擬 Switch 2 Pro，實機驗證過；非 USB。
- 沒找到任何 Pokken 描述符的 ESP32-S3 實作；沒找到 Switch 2 專屬的 OTG 供電／enumeration 坑。

## 值得借鏡的設計（PA、switch-pico）

- 指令帶「狀態 + 持續毫秒」，由裝置端保證最短維持時間，PC 不必做精準時序。
- CRC 以 session 當 seed，拒收舊 session 的殘留封包。
- 裝置回「指令完成」ack，Python 端可據此排程下一步。

## 來源

- https://github.com/PokemonAutomation/Arduino-Source
- https://pokemonautomation.github.io/SetupGuide/Controllers/Controller-ESP32-S3
- https://pokemonautomation.github.io/SetupGuide/ControllerGuide.html
- https://pokemonautomation.github.io/DeviceResearch.html
- https://newreleases.io/project/github/PokemonAutomation/ComputerControl/release/v0.55.1
- https://github.com/progmem/Switch-Fightstick/blob/master/HORI_Descriptors
- https://components.espressif.com/components/espp/switch_pro/versions/1.3.11/readme
- https://github.com/jyapayne/switch-pico
