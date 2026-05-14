# Setup Verification Report

驗證 [docs/setup.md](setup.md) 在本機（Ubuntu 20.04 + RTX 4080）的完整部署流程，並完成一次端對端會議處理（音檔 → 摘要 Markdown）。

- **日期：** 2026-05-14
- **執行者：** wilson_yeh@innodisk.com
- **環境：** Ubuntu 20.04.4 LTS, NVIDIA RTX 4080 16GB
- **LLM：** 本地 Ollama + `qwen3:4b`
- **測試音檔：** `/workspace2/wilson/sensevoice_src/team4_ap.wav`（53 分鐘，194 MB）
- **產出文件：** `/tmp/team4_ap_summary.md`

---

## 一、環境基本資訊

| 項目 | 實測值 | setup.md 建議 | 差距 |
|---|---|---|---|
| 作業系統 | Ubuntu 20.04.4 LTS | ≥ 22.04 LTS | 低於建議，實測可用 |
| GPU | NVIDIA RTX 4080 16GB | RTX 3060 12GB+ | 充足 |
| NVIDIA Driver | 535.183.06 | ≥ 525 | ✅ |
| CUDA | 12.2 | 12.2 相容 | ✅ |
| Docker Engine | 23.0.3 | ≥ 24.0 | 低於建議，實測可用 |
| Docker Compose | v2.17.2 | ≥ v2.20 | 低於建議，實測可用 |
| NVIDIA Container Toolkit | 1.16.1 | 最新 | ✅ |
| Git | 2.25.1 | ≥ 2.25 | ✅ |
| Host Python | 3.8.10 | ≥ 3.11（僅 CLI） | Web UI 流程不影響 |
| Ollama | 0.19.0 | — | ✅ |

**結論：** 即使 OS / Docker 版本低於建議下限，本專案仍能完整運行（含本地 LLM 摘要）。若是全新部署，仍建議照 setup.md 建議版本配置以降低相容性風險。

---

## 二、Pre-flight Check（setup.md 三）

| # | 指令 | 結果 |
|---|---|---|
| 1 | `nvidia-smi` | ✅ RTX 4080, Driver 535.183.06, CUDA 12.2 |
| 2 | `docker --version` / `docker compose version` | ✅ 23.0.3 / v2.17.2 |
| 3 | `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi` | ✅ 容器內可看到 GPU |
| 4 | `curl -I https://huggingface.co` | ✅ HTTP/2 200 |

---

## 三、基礎建設啟動（setup.md 四–六）

| 步驟 | 結果 |
|---|---|
| `cp .env.example .env` + `mkdir -p data/storage` | ✅ |
| `docker compose up -d --build` | ✅ 約 30 秒完成（image 已快取） |
| `docker compose ps` 5 個容器全 `Up` | ✅ redis / stt_service 標 `(healthy)` |
| `GET http://localhost:8000/` Web UI | ✅ HTTP 200, title "Meeting Assistant" |
| `GET /docs` Swagger | ✅ HTTP 200 |
| `GET /meetings`、`GET /prompts` | ✅ 正確空清單 / 4 個內建模板 |
| `GET http://localhost:8080/health` STT | ✅ `{"status":"ok","model_loaded":true}` |

---

## 四、本地 LLM 設定（setup.md 五.範例 C）

### 4.1 Ollama 與模型

```bash
# Ollama 0.19.0 已預先安裝
ollama pull qwen3:4b   # 2.5 GB，下載約 2 分鐘
```

### 4.2 啟動 Ollama（⚠️ 重要陷阱）

Ollama 預設僅聽 `127.0.0.1:11434`，Docker 容器**無法**透過 `host.docker.internal` 連到。需明確指定 `OLLAMA_HOST=0.0.0.0`：

```bash
pkill -f "ollama serve"
OLLAMA_HOST=0.0.0.0:11434 ollama serve > /tmp/ollama.log 2>&1 &
ss -tlnp | grep 11434
# 預期：*:11434（非 127.0.0.1:11434）
```

### 4.3 `.env` 設定

```env
LLM_MODEL=qwen3:4b
LLM_API_KEY=ollama            # ⚠️ 不能空字串
LLM_BASE_URL=http://host.docker.internal:11434
```

⚠️ **第二個陷阱：** `LLM_API_KEY` **不能空字串**。LiteLLM 對 OpenAI-compatible endpoint（包含 Ollama）若 `api_key` 為空，會 fallback 去找 `OPENAI_API_KEY` 環境變數，造成 `AuthenticationError`。

### 4.4 LLM 連線驗證

```bash
curl "http://localhost:8000/probe/llm?url=http://host.docker.internal:11434&model=qwen3:4b"
# {"ok":true,"detail":"reachable","models":["qwen3:4b"]}
```

✅ 連線成功，model 在清單內。

---

## 五、端對端流程（setup.md 七）

以 `team4_ap.wav`（53 分鐘技術會議錄音）驗證完整流程。

| 步驟 | 操作 | 耗時 | 結果 |
|---|---|---|---|
| 1. 建立會議 | `POST /meetings` | < 1 秒 | ✅ meeting_id 回傳 |
| 2. 上傳音檔 | `POST /meetings/{id}/audio` (`-F audio=@...`) | < 1 秒 | ✅ task_id 排程 |
| 3. STT 轉錄（`large-v3`, fp16） | `GET /tasks/{id}` 輪詢 | **約 60 秒** | ✅ 4919 字逐字稿 |
| 4. 摘要生成（Ollama qwen3:4b） | `POST /meetings/{id}/summarize` → 輪詢 | **約 20 秒** | ✅ 981 字結構化摘要 |
| 5. 產出 Markdown | `POST /documents/generate/{summary_id}` | < 1 秒 | ✅ 1755 bytes `.md` |

**音檔/處理時間比：** 3170 秒音檔 → 80 秒處理（STT + LLM）→ **約 40× 即時速度**

### 摘要品質觀察

`qwen3:4b` 對中文技術會議的處理：
- ✅ 自動識別主題（DBT/DisplayPort/Big Bang/系統測試/Q91 平台）
- ✅ 區分「會議摘要 / 關鍵決策 / 下一步行動 / 附註」四個區塊
- ✅ Action Items 表格化（任務 / 負責人 / 期限）
- ⚠️ 期限日期是模型推估，**不一定是會議實際提到的日期**（使用者需自行核對）
- ⚠️ 部分技術名詞被誤聽（如 `Westform Desktop` 應為 `Wisdom Desktop` 之類），可用詞典校正功能改善

完整輸出在 `/tmp/team4_ap_summary.md`。

---

## 六、發現的文件與設定問題

| # | 位置 | 問題 | 修正狀態 |
|---|---|---|---|
| 1 | [setup.md 二](setup.md#二作業系統與軟體需求) 版本表 | 「實測版本」欄全為 `_待填_` | ✅ 已補實測值 |
| 2 | [setup.md 六.2](setup.md#六啟動服務) `docker compose ps` 範例 | `gateway-1` 誤標 `Up (healthy)`，但無 healthcheck | ✅ 已改為 `Up` |
| 3 | [setup.md 五.範例 C](setup.md#範例-c用本機-ollama內網保密) Ollama 啟動 | 未提及 `OLLAMA_HOST=0.0.0.0`，預設綁 127.0.0.1 會讓容器連不到 | ✅ 已補充說明 + systemd 提示 |
| 4 | [setup.md 五.範例 C](setup.md#範例-c用本機-ollama內網保密) `.env` 範例 | `LLM_API_KEY=` 空字串會導致 AuthenticationError | ✅ 已改為 `LLM_API_KEY=ollama` + 註解說明 |
| 5 | [.env.example](../.env.example) Ollama 範例 | 同 #4 | ✅ 已修正 |

---

## 七、總結

`setup.md` 整體流程**可順利完成從零部署到產出第一份 Markdown 摘要**。本次修正了 5 處不準確之處（2 處顯示、3 處實際運作會卡住的細節），其中 #3、#4 是會直接擋住新使用者的關鍵 bug。

### 各階段實測耗時（image 與模型都已快取）

| 階段 | 耗時 |
|---|---|
| `docker compose up -d --build` | ~30 秒 |
| Ollama `qwen3:4b` 下載 | ~2 分鐘（2.5 GB） |
| STT large-v3 model load + 53 分鐘音檔轉錄 | ~60 秒 |
| Qwen3:4b 摘要 | ~20 秒 |
| **完整一場會議處理** | **~80 秒** |

### 未驗證（後續工作）

- 校正流程（`POST /meetings/{id}/correct` + 詞典）
- 聚合報告（`POST /documents/aggregate`）
- setup.md 二的安裝指令需在乾淨 OS / VM 重現驗證
