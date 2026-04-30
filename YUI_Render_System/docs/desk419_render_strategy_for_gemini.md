# Desk419 当前渲染方案说明（供 Gemini 分析视觉方案）

## 1) 项目目标

我们当前目标是：**不改模型结构，只做稳定渲染出图**。  
无论上游给什么 Rhino/3DM 结构（对象是否合并、命名是否混乱），渲染管线都应尽可能根据语义关键词自动匹配材质与颜色，并保证最终一定有结果图。

---

## 2) 当前技术路线（核心原则）

### 原则 A：结构无关（Structure-Agnostic）
- 不依赖固定层级编号（如 `01_` / `02_`）才能工作。
- 主要依据对象可见语义信息：对象名、集合名、导入自定义属性等。

### 原则 B：语义优先（Semantic-First）
- 用关键词识别材质类别：`Glass` / `Lens` / `Window` / `Screen` / `Metal` / `Shell` 等。
- 规则按顺序优先匹配，先命中先使用。

### 原则 C：稳定兜底（Fail-Safe Fallback）
- 若对象无法命中任何语义规则，统一进入 `fallback` 中性灰材质，避免“无材质”或渲染失败。

---

## 3) 渲染管线现状

关键脚本：
- `YUI_Render_System/src/blender_auto_pipeline.py`
- `YUI_Render_System/src/main.py`

关键 CMF 配置：
- `YUI_Render_System/config/cmf_desk419_render.json`

执行方式（示例）：
- `python src/main.py render --skip-audit --pick-3dm "assets\\Desk419 v1.6dm.3dm" --cmf-map config\\cmf_desk419_render.json`

---

## 4) 当前 CMF 规则分层（高到低优先级）

1. 撞色件（Accent/Color/Orange/Knob/Button）  
2. 玻璃件（Glass/Lens/Window/Screen）  
3. 网孔/通风件（Hole/Mesh/Grille/Vent）  
4. 金属件（Metal/Aluminum/Al/Switch/Bluetooth/Bracket/Frame/Base）  
5. 通用塑料壳体（Shell/Body/Housing/Case/Main/Front/Rear）  
6. 塑料剩余件（Main_Body/Body/Lid/Cover/Panel）  
7. Fallback 中性灰（任何未命中对象）

说明：
- 多条规则含 `exclude_keywords` 防误匹配（例如玻璃不应被金属规则吃掉）。
- 规则顺序非常关键；前面的规则会“截胡”后面的规则。

---

## 5) 已知边界与限制（请 Gemini 重点考虑）

### 限制 1：合并网格无法自动多材质
当前脚本对每个 mesh 对象只赋主材质槽位（`materials[0]`）的一种材质。  
若“前壳 + 底壳”在上游已合并成同一个 mesh，无法在不拆件的情况下自动得到两种不同外观。

### 限制 2：语义可见性依赖导入结果
如果 Rhino 图层名没有进入 Blender 可见字段（对象名/自定义属性），规则即使写对也可能命不中。  
我们已增强了 hints 收集，但仍受导入插件写入质量影响。

### 限制 3：命名噪声
对象名可能是临时字符串或乱码，导致语义匹配不稳定。  
因此 fallback 是必要保底。

---

## 6) 我们希望 Gemini 输出的内容（任务指令）

请基于上述约束，给出一套“可落地”的 Desk419 视觉方案建议，要求：

1. **输出 3 套 CMF 方向**（如：科技冷感、温润家居、工业撞色），每套包含：
   - 主体材质类型建议（塑料/金属/玻璃）
   - 主色、辅色、点缀色（建议给 RGB 或 HEX）
   - 粗糙度 roughness / 金属度 metallic / 透射 transmission 建议区间
2. **兼容结构不稳定**：方案必须可通过“关键词规则 + fallback”落地，不依赖精确拆件。
3. **给出映射策略建议**：哪些关键词应归入玻璃、金属、塑料、撞色，如何设置排除词避免误匹配。
4. **给出可执行的 JSON 调整建议**：尽量贴近 `cmf_desk419_render.json` 的字段格式。
5. **给出风险提示**：哪些视觉目标在“单 mesh 单材质”限制下无法完全达成，以及替代方案。

---

## 7) 给 Gemini 的补充上下文（可选）

- 当前渲染目标是快速稳定出图，不追求一次到位的工业级材质库精修。  
- 当前环境用 Blender Principled BSDF 自动建材，玻璃通过 `is_glass/transmission` 控制。  
- 输出重点是“可迭代的视觉方向 + 可直接改 JSON 的参数建议”。

---

## 8) 交付偏好

请 Gemini 最终输出：
- 一页结论（推荐先做哪一套，为什么）
- 三套方案参数表（颜色 + PBR 参数）
- 一版建议的 `layers` 规则顺序与关键词表
- 一段可直接替换/合并到现有 CMF JSON 的示例片段

