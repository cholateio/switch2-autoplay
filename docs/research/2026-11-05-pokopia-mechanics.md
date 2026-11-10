# Pokopia 遊戲機制研究紀錄（2026-11-05，research-scout 查證，主對話整理）

可信度中偏低：實際 fetch 讀過的只有 pokopiaguide building-techniques、Pocket Tactics moves、Serebii Magnet Rise、
Dexerto bricks；其餘為搜尋摘要（標「未驗證」）。按鍵表需實機確認。

## 建造系統

- 方塊（格子）制，可疊高（往腳下疊）。ZL+A 整齊放置；按住 ZL 移動不會走出方塊邊緣。
  https://pokopiaguide.com/guides/building-techniques（已 fetch）
- **沒有藍圖／複製貼上**。最接近：Construction Kit（Pokémon 依材料自動蓋預設房）、3D Printer（照片複製家具）。
- Magnet Rise（Magnemite，**通關後**取得）：一次最多放 3x3x1 共 9 格，可旋轉，有 Replace 模式。
  https://www.serebii.net/pokemonpokopia/magnetrise.shtml（已 fetch）。按鍵未驗證：Y 收、A 放、右搖桿按下切模式、R/B 上浮。
- 移除：Rock Smash 或破壞工具；樓梯靠近按 Y 收回（未驗證）。
- 「往下看再按 A」可放在腳下 → 鏡頭俯仰角影響放置位置，控制時要納入。
- 固定視角建造模式：未找到。Monitoring Camera 可俯瞰。
- 房屋判定：封閉空間 2x2 到 10x9（Game8，未驗證）。

## 操作（搜尋摘要，未驗證：https://thegameswiki.com/pokopia/wiki/controls-and-settings）

- 左搖桿移動、右搖桿鏡頭、ZR 用招式、ZL 按住橫移、B 衝刺、R 跳（可對調）、L 招式放射選單／滑翔、A 放置／互動。
- 十字鍵左右切換招式（Handy Bag 設「隨時可用」時無效）。
- 一般模式鏡頭能否縮放：未找到。Photo Mode 用 ZL/ZR 縮放。
- 不需陀螺儀。Mouse Mode 要拆 Joy-Con，與假手把路線無關。

## 招式當工具（https://www.pockettactics.com/pokemon-pokopia/moves 已 fetch）

- Ditto 變身成已結交的 Pokémon 學招式。Water Gun(Squirtle)、Leafage(Bulbasaur)、Cut(Scyther)、Rock Smash(Hitmonchan)、
  Rototiller(Drilbur) 都在 Withered Wastelands；Surf/Camouflage 在 Bleak Beach；Rollout 在 Rocky Ridges；Glide 在 Sparkling Skylands。
- 範圍：多為單格或十字 5 格；吃料理可升級。Rock Smash 按住 ZR 連打、靠鏡頭瞄準。Rototiller 翻土／草成田，不能翻乾土。
- 有 PP 的招式：Surf、Rollout、Glide、Magnet Rise。

## 素材與配方

- 一切在 Workbench 合成（第一個要 2 Stone）。配方來源：劇情、PC 商店（Life Coins）、Glowing Poke Ball（未驗證）。
- 磚塊：Squishy Clay（Rock Smash 打海灘黏土，或 Litter 專長 Pokémon 產出）→ Burn 專長 Pokémon（Charmander/Torchic）製成。
  https://www.dexerto.com/wikis/pokopia/how-to-make-bricks-in-pokopia/（已 fetch）
- 資料庫：thegameswiki（宣稱 714 配方）https://thegameswiki.com/pokopia/wiki/crafting-recipes ；
  Serebii https://serebii.net/pokemonpokopia/items.shtml ；Bulbapedia https://bulbapedia.bulbagarden.net/wiki/Category:Pokopia_Materials ；
  備選 PokeTools（宣稱 884，與前者不一致）、Pocket Tactics、pokopiaguide。
- 石磚牆／泥土地板／花盆／階梯的具體配方：**尚未逐項查**。

## 存檔、時間、連線

- 定期自動存檔；手動存檔會回標題畫面。每 profile 一個存檔（未驗證）。
- 即時日夜循環 + 天氣 → 影響辨識。高處遠景變藍。
- 連線（要避開）：線上 4 人同世界（NSO）、Cloud Island 島碼。

## 社群

- 展示：op.gg showcase、pokopiaguide 公開島碼。沒有動森 Nookazon 型的 build 教學站，也沒有遊戲內藍圖分享。
- YouTube build 片附材料清單：未找到證據。「5x5 Larvitar Build」查不到清單。

## 對可行性的三個風險（scout 判斷）

1. 無藍圖／複製貼上，只能單格放；Magnet Rise 9 格要通關後。
2. 放置位置取決於鏡頭角度與面向，必須靠畫面閉環校正。
3. 材料鏈長，磚塊等要靠 Pokémon 製作。
