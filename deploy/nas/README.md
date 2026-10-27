# DD會議紀錄 NAS 部署

## 資料位置
- 程式（前端＋後端，掛載）：`/volume1/docker/meetings/app`（`server/` + `web/`）
- 資料主本：`/volume1/projects/meetings`（容器內 `/data`，Telegram 機器人看到 `/projects/meetings`）
- 備份：`/volume1/docker/backups/meetings`
- 轉錄器金鑰：`/volume1/docker/meetings/worker.key`（填到電腦轉錄器設定）
- 網址：https://ddys.tail95e8ec.ts.net:9443/

## 第一次安裝（只做一次）
前置（由 Claude 用 ssh 完成）：把 `deploy/nas/` 內的檔案放到 NAS `/volume1/docker/meetings/`，並先跑一次 `update_app.sh` 讓 `app/` 有程式。

然後你只要在 NAS 的 ssh 輸入這一行（會問 sudo 密碼）：

```
sudo sh /volume1/docker/meetings/install.sh
```

它會建資料夾、產生 `worker.key`、建置並啟動容器、設定 `tailscale serve`（9443；8443 已給 XS 系統），最後印出狀態。

## 完整整理的 API 金鑰（選用）

「完整整理」是詳情頁的一個按鈕，AI 會把整場會議整理成一份完整報告。只有這一步會用 Claude API；其他 AI 處理（摘要、逐字稿整理等）仍走你的 Claude Max 訂閱。

放金鑰的方法（請自己在 NAS 上做，**不要把金鑰貼到對話或 git**）：

```
touch /volume1/docker/meetings/anthropic.key      # 先建好空檔
chmod 600 /volume1/docker/meetings/anthropic.key
vi /volume1/docker/meetings/anthropic.key          # 貼上金鑰，只放一行
```

- vi 的用法：按 `i` 進入輸入模式、貼上金鑰，按 `Esc`，再輸入 `:wq` 按 Enter 存檔離開。
- 放好或清空金鑰後不用重啟容器：每次工作開始時程式都會重新讀這個檔案。
- 這個檔案必須在 `docker compose up` 之前就存在。若不存在，docker 會自己建出一個同名資料夾，容器就讀不到金鑰；程式不會當機，但會當成沒有金鑰。
- 已經被 docker 建成資料夾時的修復方法（先刪掉空資料夾，再建檔案）：

  ```
  rmdir /volume1/docker/meetings/anthropic.key       # 只刪空資料夾
  touch /volume1/docker/meetings/anthropic.key
  chmod 600 /volume1/docker/meetings/anthropic.key
  ```

  然後照上面用 vi 貼上金鑰，再執行一次 `cd /volume1/docker/meetings && sudo /usr/local/bin/docker compose up -d --force-recreate`，讓容器改掛這個檔案（這是唯一需要重建容器的情況）。
- 沒有金鑰或檔案是空的：不會報錯，自動改用 Max 訂閱產生，畫面標示「由 Max 產生」。
- 費用：每場會議呼叫一次 Opus；會議越長越貴；「重新處理」也會再呼叫一次（切換講座／會議不會）。
- 想關閉：把檔案清空（`: > /volume1/docker/meetings/anthropic.key`），不用重啟容器。

## 更新程式
在專案根目錄（Git Bash）執行：

```
sh deploy/nas/update_app.sh
```

它把 `server/` 與前端檔案用 tar over ssh 傳到 NAS、舊的 `app` 改名成 `app.old-時間`（不刪除）、新增 `.restart` 讓容器自己重啟。不需要 sudo、不需要重建映像。
只有改 `Dockerfile` / `requirements.txt` 時才需要重新 `sudo ... docker compose up -d --build`。

## 看紀錄
```
sudo /usr/local/bin/docker logs --tail 100 meetings
```
