# 日报 2026-05-14

## 今日讨论与结论

---

### 1. 无参照物单图判断衣服版型

**背景**：衣橱建模时用户上传单张衣物图，没有人体/尺子作为参照物，讨论 vision 模型能否判断版型。

**结论**：
- 版型判断不依赖绝对尺寸，靠**衣服内部比例关系**即可完成分类
- 可靠推断：宽松/修身/oversize、直筒/A字/X形、短款/常规/长款
- 不可推断：精确三围尺寸、对特定用户体型的适配度
- 对衣橱建模够用：只需标签分类（修身/宽松/A字…），GPT vision prompt 加一行 `"fit"` 字段即可
- 落地位置：接入 OpenAI vision 后，在 `phase1_wardrobe.py` 的识别 prompt 和 `wardrobe` 表各加 `fit` 字段

---

### 2. 会议纪要解读：小模型 + 大模型分级处理

**背景**：mentor 在会议中建议「尝试动态路由或分级处理，用小模型先处理再用大模型加工」，结合一丹测试发现的问题（衣服定义不明确、文字镜像反转、多轮背景颜色改变）进行解读。

**解读**：
在图像生成前加一层 **prompt 工程层**：

```
用户输入（模糊）
  → [小模型：GPT-3.5 / Claude Haiku] 结构化 + 补全 + 注入负向词
  → [image2] 生成
```

小模型负责三件事：
1. 补全衣服定义（长款/短款、宽松/修身、材质等用户没说清楚的属性）
2. 注入固定负向词（`no text, no mirrored text, white background`）→ 直接解决镜像和背景漂移问题
3. 多轮对话时把上一轮已确定属性带入下一轮，保持一致性

成本极低（纯文字），但能显著提升 image2 生成稳定性。

---

### 3. 动态路由概念

**定义**：根据请求复杂度/类型，自动决定走哪条处理链。

| 请求类型 | 判断 | 处理路径 |
|---|---|---|
| 天气 + 场合 → 今日推荐 | 规则可解决 | 直接走过滤规则，0 成本 |
| "适合去画廊的有艺术感穿搭" | 语义复杂 | 调 GPT 推理 |
| "把这件换成砖红色" | 明确操作 | 直接 image2 换色 |
| "生成一套秋天约会穿搭" | 创意生成 | 两阶段：小模型补全 → image2 |

路由 + 两阶段结合：**路由先判断走哪条链，需要生成的才进两阶段流程**。核心价值：节省成本 + 提升响应速度。

---

### 4. 架构实现与量化验证

**模型选型：Groq（llama-3.3-70b）**

router 和 prompt_builder 两个模块都使用 Groq，不使用 OpenAI，原因：

- **任务轻**：意图分类输出一个词，prompt 补全输出一段 JSON，不需要 GPT-4 级别的推理能力
- **速度快**：Groq 基于自研 LPU 芯片，实测 router 单次调用 250-800ms，比等 image2 的几分钟完全可忽略
- **免费**：Groq 免费额度（每天数十万 token）覆盖 demo 阶段全部用量
- **可替换**：接口与 OpenAI 兼容，正式上线换一行 base_url 即可迁移

**新增文件：**
- `router.py`：Groq LLM 意图分类，输出 `rule / image_gen / recolor / tryon / unknown`，网络异常时规则降级兜底
- `prompt_builder.py`：Groq LLM prompt 补全，提取 type/color/season/style/fit，注入固定负向词，支持多轮 context
- `eval_router.py`：8 条测试用例覆盖简单/中等/复杂/否定语义/多轮场景

**接入主流程：**
- `phase2_tryon.py`：换色 prompt 末尾自动注入负向词
- `main.py`：新增 option 8「自由描述生成图片」，走完整 router → prompt_builder → image2 链路

**架构：**
```
option 8 用户输入
  → router.py（Groq LLM 意图分类，~250-800ms）
  ├── rule    → phase3 推荐
  ├── recolor → phase2 换色
  ├── tryon   → phase2 试穿
  └── image_gen → prompt_builder（Groq LLM 补全）→ image2 生成（1-5分钟）
```

---

### 5. 量化验证结果

使用 **Groq（llama-3.3-70b）** 作为测试 LLM（免费，后续替换为 OpenAI），与无 router 的 baseline 对比：

| 维度 | Baseline（原始输入直接给 image2） | LLM Router + Prompt Builder | 提升 |
|---|---|---|---|
| 路由准确率 | — | 8/8 = **100%** | — |
| 属性完整度 | 0% | **52%** | +52% |
| 负向词覆盖率 | 0% | **100%** | +100% |
| 期望属性命中率 | 67% | **100%** | +33% |

**关键验证点：**
- **负向词 0%→100%**：`no mirrored text / white background` 全部注入，镜像和背景漂移问题从 prompt 层解决
- **否定语义（T07："不要太正式"）**：baseline 错误提取"正式"，LLM 正确推断为 `['休闲', '文艺']`
- **模糊语义（T06："秋天约会穿搭，有点文艺感"）**：无触发词，LLM 正确识别为 `image_gen`，规则版做不到

---

---

### 6. wardrobe 表新增 fit（版型）字段

**背景**：单张衣物照片无参照物时，vision 模型可通过衣服内部比例关系判断版型，结论是用标签分类（修身/宽松/A字…）即可，不需要绝对尺寸。

**改动：**
- `db.py`：`wardrobe` 表新增 `fit TEXT` 列；ALTER TABLE 迁移兼容旧库；`get_all_wardrobe_items` / `get_wardrobe_item` 改为显式列名 SELECT，避免列序问题
- `phase1_wardrobe.py`：mock 识别结果加 `fit` 字段；入库时透传；`list_wardrobe` 展示版型

**接入 OpenAI vision 后**：在识别 prompt 里加一行 `"fit": "修身/宽松/oversize/直筒/A字 选一个"` 即可，数据库结构不用再改。

---

---

### 7. 架构落地化：CLIP + FAISS 闭集检索方案

**背景**：PRD 中"摄像头低清→闭集匹配衣橱"功能此前只有产品描述，缺工程实现方案。今日确认技术路线。

**核心范式转变**：
- ❌ 不是：开放世界衣物分类（open-world classification）
- ✅ 而是：用户衣橱内闭集检索（closed-set retrieval）

难度下降一个量级，低清摄像头（320×240 / 20kb）完全可行。

**技术方案：**
```
入库时：GPT Vision → 属性 JSON + OpenCLIP → item_embedding → FAISS
每日帧：CLIP embedding(frame) → FAISS Top-K → 匹配衣橱单品
```

**新增工具（待接入）：**
- `Marqo FashionSigLIP`（via open_clip）：衣物 embedding 生成，最终选型见第 13 节
- `FAISS`：本地向量索引检索
- `SAM2`：人体分割，供 Mask inpainting 路径使用

**Embedding 双层设计：**
- `item_embedding`：单件衣物，闭集匹配 + 相似搜索 + 风格聚类
- `outfit_embedding`：整套穿搭，P2 风格分析 + 搭配相似度排序

**延伸价值（CLIP 不只是识别）：**
- 相似单品推荐（embedding similarity）
- 风格自动聚类（无需 LLM 总结）
-「你真正会穿的衣服」报告（embedding distribution analysis）
- Outfit compatibility 排序

**PRD 更新**：Section 2 摄像头技术实现补全、Section 5 工具表新增三项、加 embedding 策略说明。版本已更新至 prd_v0.3.md。

---

### 8. CLIP retrieval feasibility test 实测

**背景**：clip_retrieval_test.py 跑出来两轮，分析结果并持续优化。

**第一轮（ViT-B/32，8 张图）：62%**

混入了生成输出（gen_/recolor_/tryon_/test_ 前缀），误召回不是模型能力问题而是测试集污染：
- `b2a038...jpg` vs `pinktop.jpg`：两张是同一粉色上衣，相似度完全相同（0.768 tie），CLIP 其实认出来了
- `gen_*` 图被 `test_*` 召回：AI 生成图风格相似，embedding 落在同一区域

**根本原因 → 代码修复**：
- FAISS 索引只应存真实入库衣物，不能混入生成输出
- 修改 `clip_retrieval_test.py`：优先从 DB 读 `source='real'` 记录，DB 不足 2 条时 fallback 扫 images/ 目录并过滤生成前缀（gen_/recolor_/tryon_/beautify_/test_）
- DB 路径 `image_url` 为空或不存在时降级逻辑同步修复

**第二轮（ViT-B/32，13 张真实图，删掉重复粉色上衣后）：46%**

失败分析：
- `img_v3_c38e...` 和 `img_v3_5ea9...`、`IMG_3829/3830/3831/3832` 之间多次互相混淆 → 这批图本身视觉差异偏小（都是相近风格/色系）
- `image.png` vs `image1.png` 相似度高达 0.804 → 这两张可能是同款

**关键认知**：ViT-B/32 精度弱，46% 不代表路线不行，正式判断需要 ViT-L/14。

**第三轮（ViT-L/14，12 张图）：25%，比 ViT-B/32 更低**

反直觉但有解释：

- `IMG_3832.JPG` 成为"全局吸铁石"，12 次 query 中有 8 次它排 Top1 → 这张图 embedding 落在向量空间中心，是视觉上最"通用/平均"的衣物图
- `IMG_3829/3830/3831/3832`（连续序号）和 `img_v3_0211l_*`（共享 ID 前缀）两组图，极可能是同一批同风格拍摄 → 它们真的视觉相近，CLIP 的判断是对的
- ViT-L/14 精度更高 → 对视觉相似度更敏感 → 这批相近图被它看得更"像"，Top-1 区分度反而下降

**结论：这是测试集质量问题，不是模型或路线问题。**

测试集需要**品类/颜色/风格差异明显**的图（如白 T、黑西裤、蓝牛仔外套、花裙、卡其大衣各一张）才能得到有意义的准确率，待补充。

> 模型后续升级为 Marqo FashionSigLIP（2025），见第 13 节。

**PRD v0.3 同步更新**：
- Section 2：技术实现扩展为完整 5 步流程（含 synthetic on-body 冷启动增强）+ 多来源 embedding 数据结构（flat_lay / synthetic_onbody / real_onbody[]）+ retrieval vs 识别对比表 + synthetic on-body 使用边界说明
- Section 5：工具表 OpenCLIP 拆为三行（ViT-L/14 主力 / ViT-B/32 baseline / SigLIP 对照）+ 新增模型选型建议表 + Feasibility Test 验收标准（Top-1≥70% 自动写入，Top-3≥90% 一键确认）

---

---

### 9. A+B=C 试穿端到端验证

**测试方式**：`python test_tryon.py <全身照> <衣物图>`，不依赖 DB 和衣物数据，直接验证 image2 生成链路。

**结果**：job 成功提交（`job_id=93d634c8...`），轮询中，生成结果待收。

**test_tryon.py 设计**：构造最小 item dict → 调 `_build_tryon_prompt` → 展示 prompt 预览 → 确认后提交，用于快速验证 prompt 和服务，不写入 DB。

---

### 11. 衣橱数据格式 v1.0 & 批量导入工具

**背景**：同事负责处理博主衣橱数据（白底美化 + 打标），需要统一交付格式。

**`data_format_v1.md`**（新建）：
- 字段：`category / type / raw_type / color / style / season / warmth / fit / description / image`
- `warmth` 字段（薄/中等/厚/不适用/无法判断）比 season 更实用，直接映射天气推荐
- `raw_type` 保留原始识别名（如"屁帘"），`type` 做标准化映射，无法映射填"其他"
- 两个示例（标准单品 + 非标准单品）

**`import_batch.py`**（新建）：读取 `items.json` + `images/` 目录，校验枚举字段，复制图片，批量写入 DB，打印成功/跳过/失败统计。

**`db.py` 同步更新**：新增 `category / raw_type / warmth` 列，ALTER TABLE 迁移兼容旧库。

---

### 12. PRD v1.0 完成

**背景**：会议明确 MVP 交付范围后，从零起草新 PRD（独立于 prd_v0.3.md），结合 UX 设计稿补全所有模块。

**产品层级架构（最终）：**
```
Level 0  全局 AI Hub（Tomorrow Planning 推送 / 天气 / 日历事件触发）
  └─ Level 1  Fashion Agent（衣橱 + 风格记忆 + 洞察首页）
       └─ Level 2  Styling Session（沉浸式对话试穿）
```

**核心设计决策（讨论后确认）：**

| 决策 | 结论 |
|---|---|
| 主动推送触发方式 | 每晚 8 点固定 Tomorrow Planning，不做实时出门检测（creepy + 不可靠） |
| 推送优先级 | S: 8 点 planning，A: 天气剧变，B: 日历特殊事件，每天最多一次 |
| 摄像头 OOTD v1.0 | 用户主动按硬件键触发，镜子自动检测留后续版本 |
| 单品图存储 | `image_url`（白底展示图）+ `image_crop_url`（原始 crop，CLIP 用），对用户透明 |
| 确认粒度 | 最少确认 category + type，其余 tag 静默存入可后改 |
| 截图导入 | v1.0 直接 GPT Vision 打标，不做 inpaint 预处理 |
| AI 语气 | brief / tasteful / minimal，不做陪伴人格化；说明文字默认极短，点开才展开 |
| Color Analysis | 降级为 setup 入口，非首页一级；第一次进推荐时一次性引导卡片 |
| Inspiration | v1.0 不做 |

**v1.0 功能范围**：衣橱建模（三入口）/ Styling Session / Looks 日志 / Tomorrow Planning / 摄像头 OOTD（硬件键）/ Style Identity（弱化版）/ Color Analysis（setup 入口）

**后续版本规划新增**：Styling 细节调节（拉链/下摆塞入）/ 社交对象关联（见同一人不重复穿）/ 音频沉淀个人冷热感知 / 音频捕捉他人 OOTD 评价

---

### 13. 检索模型升级：Marqo FashionSigLIP

**背景**：CLIP ViT-L/14（2021）已非最优方案，调研 2025-2026 最新 fashion retrieval 模型。

**选型结论**：**Marqo FashionSigLIP**（2025）— 在 100 万+ 时尚商品图上 fine-tune，专门优化颜色/材质/类目/细节，比通用 CLIP 在 fashion 检索上高 57%，开源，open_clip 直接加载。

**备选**：SigLIP 2（Google，2025 年 2 月）— 通用底座 fallback。

**改动**：`clip_retrieval_test.py` model name 改为 `hf-hub:Marqo/marqo-fashionSigLIP`，FAISS 逻辑不变。PRD 技术方案同步更新。

---

## 当前实现状态

### 已完成（后端）

| 模块 | 文件 | 状态 |
|---|---|---|
| 数据库 | `db.py` | 完整，含 category / raw_type / warmth 字段 |
| 衣橱建模 | `phase1_wardrobe.py` | 核心逻辑完整，GPT Vision 打标接口预留（API key 待拿） |
| 试穿 A+B=C | `phase2_tryon.py` | 完整，含 prompt 构建 / 场景自动选择 |
| image2 客户端 | `image2_client.py` | 可用，需关闭 VPN |
| 批量导入 | `import_batch.py` | 完整 |
| 数据格式规范 | `data_format_v1.md` | 已发同事 |
| CLIP 检索 | `clip_retrieval_test.py` | 模型升级为 FashionSigLIP，待跑验证 |

### 待开发

| 模块 | 说明 |
|---|---|
| 前端 | `app.py` + templates 完全空白，所有界面待写 |
| Onboarding 流程 | — |
| Tomorrow Planning | — |
| Looks 日志页 | — |
| Style Identity | — |
| Color Analysis | — |

### 待集成（后续）

`router.py` + `prompt_builder.py`（Groq LLM 意图分类 + prompt 补全）目前在 `demo/` 目录，MVP 未接入。当前 `phase2_tryon.py` 的 prompt 是写死的规则逻辑。后续接入 router 后可支持用户自由描述生成穿搭，prompt 质量也会提升。

### 下一步

写 Flask `app.py` + 页面模板，搭出可交互的 MVP 前端。
