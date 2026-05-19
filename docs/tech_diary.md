# Tech Diary — 架构和设计问题记录

> 记录开发过程中踩的坑、设计错误、以及最终怎么改的。

---

## 2026-05-20

### 问题1：推荐穿搭每次重启都重新生图

**现象**：服务重启后，首页推荐图每次都重新触发生成，明明上次已经生好了。

**根本原因**：
- 每次用户添加单品，都调了 `clear_cache()`，把 outfit 元数据 + 图片路径一起清掉，然后存到 JSON
- 重启后从 JSON 恢复，outfit 元数据有，但 `image_cache` 是空的
- 所以每次重启后首次访问，都判断"没有图片缓存"→ 触发重新生图

**第二个坑**：原来的图片复用逻辑是 `if old_outfits != new_outfits` 全量比较字典，但 outfit 里有 `caption` 字段是 LLM 每次新生成的，所以永远不相等，图片永远被判定为"需要更新"。

**怎么改的**：
1. 把"单品增删"和"换主照"分开处理
   - 换主照 → `clear_cache()`，全清（人变了，所有图都作废）
   - 单品增删 → `invalidate_outfits()`，只打一个 stale 标记，**不动图片缓存**
2. 比较逻辑从全量 dict 比较改为只比 `item_ids` 集合，caption 变了不算数

**结果**：添加单品后，如果推荐的组合没变（新单品没进推荐），图片直接复用，不重新生图。

---

### 问题2：生图挂起 5 分钟以上

**现象**：quick-tryon 提交后一直轮询，5 分钟还没结果。

**根本原因**：用户上传的衣物照片是手机原图（5712×4284，24MP），`_to_rgba_png()` 转成 RGBA PNG 后变成 **15MB**。连同用户全身照，两张图合计 30MB+ 上传到 OpenAI，上传本身就要好几分钟。

**怎么改的**：
- `_to_rgba_png()` 里加 resize，超过 1536px 自动等比缩小
- 15MB → 1.9MB，上传时间从几分钟降到几秒
- 同时给 OpenAI client 加了 `timeout=300`，防止真正挂死

---

### 问题3：多件单品 beautify 后都生成了同一张图

**现象**：一张照片里有 T恤、短裤、运动鞋三件，beautify 后三个单品卡片显示的图一样（全都是那件运动鞋的 flat lay）。

**根本原因**：`beautify_image()` 的 prompt 没有指定要提取哪件，模型自己随机选一件，三次结果可能一样也可能不同。

**怎么改的**：
- `beautify_image()` 加了 `description` 参数
- 调用时传入单品描述，比如"黑色运动鞋"、"灰色T恤"
- 每件单品的 beautify prompt 明确告诉模型"提取这一件"

---

### 问题4：`--reload` 模式开发时会杀死生图任务

**现象**：修改代码文件保存后，uvicorn 自动热重载，正在生图的后台任务被中断，前端一直轮询直到"任务不存在"。

**原因**：`--reload` 检测到文件变动就重启进程，所有内存任务（`_tasks` dict）和后台线程都销毁了。

**解决方式**：代码稳定后去掉 `--reload`，用 `uvicorn app:app --port 8000 --host 0.0.0.0` 启动，改代码后手动重启。

---

### 问题5：outfit 推荐缓存 key 包含温度档位

缓存 key 是 `(user_id, date_iso, temp_bucket)`，温度分 cold/cool/warm/hot 四档。
气温在档位边界浮动时（比如 15.5°C 变 16°C 跨越 cool→warm），会触发重新推荐 + 重新生图。
目前接受这个 trade-off，是合理的业务逻辑（气温变化推荐也该变）。

---

## 2026-05-20（续）

### 问题6：多个 beautify 任务并发导致最后一个挂死

**现象**：一张照片识别出 6 件单品，全部触发 beautify，加上同时有推荐穿搭生图，共 7 个 `images.edit` 同时提交，最后那个任务一直轮询不出结果，最终挂死。

**根本原因**：OpenAI `images.edit` 有隐性并发限制（约 2-3 个/key），超出后不报错，而是静默排队。排在后面的任务实际上在 OpenAI 那边等，等到客户端 `timeout=300s` 到期，就挂死。服务端的 `_beautifying` set 里还留着这个 item_id，重启前无法重新触发。

**怎么改的**：
- `image2_client.py` 加全局 `threading.Semaphore(2)`
- `generate()` 函数用 `with _GENERATE_SEM:` 包住整个生成逻辑
- 第 3 个任务会在**本地**等信号量，不会被 OpenAI 静默挂起，等前面的完成再提交

**结果**：并发任务变成本地有序排队，每次最多 2 个同时跑，不再挂死。

---

### 问题7：推荐生图重复触发 3 次（竞态条件）

**现象**：推荐图生成失败（Connection error）后，用户多个页面/标签同时刷新，服务器日志里出现 3 条 `[gpt-image] 提交任务 prefix=grid`，同一套推荐被白做 2-3 次。

**根本原因**：`/api/recommend` 里判断"是否已在生图"用的是 check → act 两步：
```python
if is_generating(key):   # 检查
    ...
else:
    mark_generating(key, True)  # 设置（晚了一步）
    background_tasks.add_task(...)
```
两个请求同时通过检查，各自都以为自己是第一个，各自都触发了生图。

**怎么改的**：
- `outfit_recommender` 加 `threading.Lock()` + `claim_generating(key)` 函数
- 把 check + set 合成一个原子操作：拿到锁才能把 key 写进 `_GENERATING`，返回 `True`；已存在则返回 `False`
- `app.py` 改为：`elif outfit_recommender.claim_generating(key): 启动任务`，拿不到直接返回 `running`

**结果**：不管多少个并发请求同时进来，只有一个能成功 claim，其余直接返回 running 等待。
