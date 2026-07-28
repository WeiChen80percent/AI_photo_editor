# 語意詞彙、語言與新參數擴充指南

本文件描述目前 `semantic_registry_v1` 的實際擴充契約。核心原則是：

> 擴充「使用者怎麼說」時改 registry 資料；擴充「系統能調什麼」時再補 schema、policy、render、前端 metadata 與測試。不要用完整句 regex 或依 axis 名稱分支的 `if-else` 擴充 parser。

## 目前 production 狀態與驗證基線（2026-07-26）

Production semantic takeover 已啟用：

- `semantic_registry` 產生完整、fully resolved 且通過 validator 的 IR
  時，直接交給 adaptive controller，不再回到 legacy 句型 parser。
- Deterministic semantic parser 明確拒絕時，該拒絕具有權威性；legacy
  parser 不可覆蓋或局部套用。
- 只有不符合上述 accepted / rejected takeover 邊界的情況，才保留
  legacy compatibility 路徑。
- 因此擴充 registry、scope 或 validator 已是 production 行為變更，
  必須同時檢查 success contract、fail-closed contract、controller
  state、render vector 與 history 原子性。

- 先前 alias audit 找出的 16 個 semantically-loaded `noise` 已全部
  改為 typed roles：region context/support/anaphora、content
  constraint、semantic attribute、scope quantifier、compound marker
  與 return relation。新增語言時應擴充這些既有 concept，不要把
  翻譯重新塞回 `noise`。
- Registry 中所有 remaining noise 都有自動 prefix / suffix
  metamorphic contract；加上或移除 noise 不得改變 canonical IR。
- `not <descriptor/effect> enough` 已是跨 axis 的 typed composition：
  `negation + grounded descriptor/effect + sufficiency modifier`。中文
  `不夠 / 不足 + descriptor` 走同一類 observation contract。新增
  axis 時補 aliases / effect metadata 即可，不需新增句型。
- Effect polarity、quantity-only `all`、contextual `shot/frame` 與
  parent-local safety 都由 registry metadata、span 和 provenance
  決定。無法唯一證明的 anaphora、constraint、attribute、compound、
  return 或 sufficiency relation 必須 fail closed。
- Validator 會用同一 registry exact re-resolution；grounded LLM
  不能自行宣稱 typed relation 已被合法消耗。
- Parser-safety 階段曾完成 **241 / 241 passed**；這是 takeover 前的
  歷史基線，不應取代目前 production regression。
- 目前已核對的 production focused evidence 包含：
  - semantic / parser / scope / validator / compiler / route /
    typed-context 與關鍵 adaptive：**252 / 252**；
  - effect group、assembler、validator、controller contract：
    **107 / 107**；
  - public API rejection：**4 / 4**；
  - contrastive bound：**11 / 11**；
  - 真實 SegFormer integration：**2 / 2**；
  - frozen holdout：**363 / 363**；
  - LLM shadow：**667 / 667**；
  - Gate 10：**5 / 5**。
- 最新 parser-safety suite 為 **324 / 326 PASS**。2 個 FAIL 都是
  generated frozen metadata 計數漂移：registry 現產生 **787** 個
  cases，但舊 expected 仍為 **789**；directional aliases 現為
  **157**，舊 expected 為 **159**。目前沒有 runtime semantic
  mismatch，但 current full suite 仍不能宣稱全綠。
- Axis-neutral observation → remedy focused regression：
  **4 / 4 PASS**。中文、英文、多軸與 `sky` 局部正例均通過；與
  observation 相反的 remedy 會原子拒絕為 structured `422`。
- 上述 suite 彼此有重疊，不能相加成唯一測試總數。
- Compact adaptive 指定的 50 tests 目前為
  **2 failures + 6 errors**：5 個 negated-context 與 1 個
  overexposure outcome guard 目前安全地 fail closed 為 `422`，
  但與舊 success contract 不同；2 個 failures 是 bare strong
  descriptor 的跨語言 expectation 歧義。
- 最新效能：normal prompt p95 **2.636625 ms**，低於 10 ms 門檻；
  2,000 code-point legal / adversarial p95 分別為 **133.319 ms** /
  **173.975 ms**，高於 120 ms 門檻。依目前產品政策只記錄此
  performance gap，本階段不延伸深挖。
- 因此目前可以宣稱「production takeover 已啟用，主要 focused
  safety / integration 證據通過」，不能宣稱「所有 legacy regression
  與人工驗收全部完成」。
- 風格與新參數擴充仍是後續 registry / capability 工作，不因本階段
  接管而刪除。

## 先判斷是哪一種擴充

| 需求 | 應修改的位置 | 不應修改的位置 |
| --- | --- | --- |
| 既有參數增加同義詞、動詞、形容詞或觀察詞 | `semantic_registry.py` 的該 axis aliases、少量 test seeds | normalizer、slot extractor、scope resolver、assembler |
| 既有概念增加一種語言的說法 | 該 axis、region 或 shared concept 的 `ConceptAlias` / `AxisAlias` | 另一套語言專用 parser |
| 既有 typed relation 增加新表面詞 | 對應的 region context/support/anaphora、attribute、quantifier、compound、return、sufficiency 或 effect concept | `noise`、完整句 denylist |
| 新 axis 要支援 `not ... enough` / `不夠...` | 該 axis 的 descriptor/observation aliases；必要時補集中式 effect binding | 為該 axis 新增 sufficiency `if-else` |
| 驗證未來 axis 可被 parser 自動理解 | 測試內建立 `synthetic_axis` registry | production parser 與 OpenCV handler |
| 新增可真的改變像素的正式參數 | schema、policy、registry、render contract、OpenCV handler、mapper neutral、前端公開 metadata、測試 | 只加 alias 後就宣稱功能完成 |
| 新增 region / mask | region-mask schema、registry region aliases、render contract、實際 mask handler、整合測試 | 找不到 mask 時退回全圖 |

目前 deterministic pipeline 是：

`normalizer → registry-driven slot extractor → generic scope resolver → generic operation assembler → semantic validator → adaptive controller → OpenCV`

語言 alias 是資料；parser 核心只處理 slot、span、scope、衝突與驗證。

## 1. 只替既有參數增加 aliases 與 seed

### 1.1 先選正確的資料類型

在 `backend/app/services/semantic_registry.py` 的 `DEFAULT_AXIS_ALIASES` 找到既有 axis，增加 `AxisAlias`：

- `role="axis"`：參數名詞，例如 brightness / 亮度；本身不要求方向。
- `role="positive"`：該 axis 的正方向表面詞。
- `role="negative"`：該 axis 的負方向表面詞。
- `match_kind="action"`：真正表示操作的動詞。
- `match_kind="descriptor"`：表示狀態或比較結果的形容詞；positive / negative alias 的預設值。
- `match_kind="observation"`：描述目前問題、需要由通用 scope 規則轉成修正方向的觀察詞。
- `direction_multiplier=-1`：只可用在 `role="axis"` 的反向量名詞；例如表面上的「增加 haze」對 dehaze 軸代表負方向。不要用它修補整句特例。

加入 alias 時：

1. 使用最小、可重用的概念片段，不放入完整使用者句子。
2. 指定實際語言來源；language tag 會保存在 evidence 中。
3. 先用 `normalize_alias_text()` 想像 runtime canonical form，避免只靠大小寫、全半形、dash 或 Unicode 變體區分兩個意思。
4. 若詞彙其實是所有 axis 共用的方向、強度、否定、連接、numeric relation、reset、region scope、mechanism 或 noise，應加入 `DEFAULT_SHARED_CONCEPTS`，不要複製到 11 個 axis。
5. 若只是既有 region 的另一種稱呼，應加到 `DEFAULT_REGIONS` 對應的 `RegionDefinition`，不要把 region 詞彙塞進 axis。

`noise` 只允許真正語意不變的 wrapper。若表面詞帶有以下任一資訊，
必須使用現有 typed slot 或新增一個通用 slot contract：

- whole-image context 或 parent-local override；
- region noun、anaphora、subject/content constraint；
- attribute、scope quantifier、compound completeness；
- return / existential / sufficiency relation。

新增或翻譯 noise 後，必須讓 registry 衍生的 prefix / suffix
metamorphic test 全綠；不能只因 parser 當下「仍可解析」就把詞當成
noise。

禁止用以下方式修案例：

```python
# 禁止：完整句 template
re.compile(r"make the background more saturated")

# 禁止：axis-specific parser branch
if axis == "saturation" and "background" in prompt:
    ...
```

正確方向是分別註冊 `background`、`more`、`saturated` 等可重用概念，讓通用 scope 演算法依 span 與關係組裝。

### 1.2 補少量人工 seed

在同檔案的 `DEFAULT_TEST_SEEDS` 為該 axis 增加或調整 `AxisTestSeed`：

```python
AxisTestSeed(
    text="<一個可獨立判定的自然短句>",
    language="<language tag>",
    expected_direction=1,       # 只允許 -1 或 1
    expected_strength="normal", # subtle / normal / strong
)
```

seed 的目的不是列完所有句型，而是提供最小人工契約：

- 至少能唯一解析出宣告的 axis 與 direction。
- strength 若不是 `normal`，必須明列。
- 不要把 holdout 中失敗的整句直接複製成 seed。

registry 建立時會驗證每個 seed 是否真的只指向宣告的 axis 與 direction；生成式測試再驗證完整 semantic IR。

### 1.3 驗證

從 `backend/` 執行：

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' -m unittest `
  tests.test_semantic_registry `
  tests.test_semantic_slot_extractor `
  tests.test_semantic_scope_resolver `
  tests.test_semantic_operation_assembler `
  tests.test_semantic_validator `
  tests.test_semantic_parser
```

接著跑 registry 衍生的生成式 corpus 與人工 holdout：

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  tests/run_semantic_generated_report.py

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m unittest tests.test_semantic_holdout
```

生成式案例可以隨 registry 合理增加；holdout 的 case id、prompt 與人工 expected 不可為了提高通過率直接改寫。若 holdout 失敗，要修共用 concept、scope 或 validator，不能新增整句特例。

## 2. 增加新語言的 concept

目前 runtime 會同時掃描所有 language aliases；language tag 是 provenance，不是互相隔離的 matching namespace。因此新增語言不需要新增 `ChineseParser`、`EnglishParser` 或第三套 parser。

### 2.1 只有新翻譯或同義詞

依概念所屬位置增加資料：

- axis 名稱、axis-specific action / descriptor / observation：`AxisAlias`。
- region 稱呼：對應 `RegionDefinition.aliases` 的 `ConceptAlias`。
- direction、strength、negation、guard、conjunction、reset、numeric relation、scope、noise 等共用詞：對應 `SharedConceptDefinition.aliases` 的 `ConceptAlias`。

一種新語言要能組成完整操作，至少要審核：

1. axis 正反向詞。
2. 共用 direction 與 strength。
3. region 與 region scope。
4. negation / guard / conjunction。
5. reset 與 numeric relation。
6. 常見但不改語意的 wrapper / noise。

不必為每個 axis 重複加入「請、幫我、稍微」等共用詞。

### 2.2 真正新增一種 semantic slot / value

如果不是翻譯，而是新增一種以前不存在的語意關係：

1. 在 `_SHARED_SLOT_CONTRACTS` 宣告 slot 的固定型別與 allowlisted values。
2. 用 `SharedConceptDefinition` 註冊概念與 aliases。
3. 只在 scope resolver / assembler 增加一次「這種關係如何作用」的通用規則。
4. 為該關係寫跨 axis 測試，不能只對一個 prompt 或一個 axis 生效。

這類修改是擴充語意模型本身，不是句型修補。若現有 slot 已能表達需求，就不要新增 slot。

### 2.3 擴充 sufficiency / effect 說法

不要為 `not bright enough`、`not warm enough`、`不夠清楚` 等每個
axis 建立不同句型。現有 resolver 會通用組合：

1. typed negation；
2. 唯一且 grounded 的 axis descriptor 或 effect state；
3. `observation_modifier=not_enough`；
4. registry 中的 descriptor direction 或 axis-effect polarity。

要讓新 axis / 新語言支援此關係：

- 為 axis 增加可唯一落軸的 descriptor / observation alias；
- 若表面詞描述的是效果而非 axis，加入集中式
  `EffectDimensionDefinition`、`EffectStateAlias` 與
  `AxisEffectBinding`；
- 為正向、反向、無 negation、重複命令、相反 polarity 與
  unsupported effect 補 paired controls；
- 驗證 exact evidence spans、validator re-resolution、root /
  parent-local parity 與 422 零 side effects。

只有 `negation + descriptor/effect + sufficiency` 的 support graph
唯一時才能消耗 negation。單獨 `bright enough`、否定命令、
同 clause 方向衝突、沒有 binding 的 effect 都應拒絕；不得靠 LLM
補造 support。

### 2.4 `effect_reference → group_feedback` 的通用契約

「去霧效果太重」、「銳化效果太重」、「提亮效果太重」這類輸入，
不應為每種效果建立句型或 axis 分支。Production assembler 只在下列
typed evidence 同時成立時，才建立 `group_feedback`：

1. 同一 operation group 中恰好有一個可唯一綁定的
   `effect_reference`；
2. 存在 typed overdone observation，例如「太重」、「過頭」或
   `too strong` / `too much`；
3. registry 中該 axis 確實有 macro-capable alias，可指向既有
   contribution group。

`target_group_intent` 必須由該 axis 的
`AxisPolicy.positive_intent` / `negative_intent` 依修正方向推導，
不可從原始 prompt 猜測，也不可用 axis 名稱的 `if-else` mapping。
Validator 會獨立重做 scope、effect span、observation、
macro capability、修正方向與 target intent 的 exact
re-resolution；grounded LLM 或 assembler 不能自行宣稱這些條件已
成立。

目前通用正向案例已涵蓋去霧、銳化、提亮、暖色與鮮豔效果太重。
下列對照案例則必須維持原本語意：

- 「去霧效果減一點」是明確的單軸調整，不是 group feedback。
- 「清晰度效果太重」若只有 non-macro observation，不得建立群組。
- 「去霧太重」缺少 `effect_reference`，不得僅靠相鄰詞推測群組。

Group feedback 的作用是縮放同一既有 contribution group 中已存在的
companions；它不會憑空加入 prompt 沒有要求、parent ledger 也沒有的
軸。新增效果詞時應補 effect binding、macro capability 與 paired
controls，不能把失敗整句加入 parser template。

### 2.5 Normalizer 的界線

`semantic_normalizer.py` 只處理文字機械正規化：2,000 Unicode code-point 上限、NFKC / casefold、空白、標點、英文 contraction、少量可人工審核的字形等價，以及 raw offset 還原。它不知道 axis、region 或完整句子。

只有真正跨領域、機械性的正規化才應加入 normalizer。參數同義詞、語法、翻譯或完整句改寫必須留在 registry / scope 層。

### 2.6 語言與 collision 驗證

registry 會 fail fast：

- 同一語言、同一 namespace 內的重複 axis / region alias。
- 同一 shared slot 內的重複 alias。
- 經 runtime normalization 後，不同語言卻映射到不同 semantic binding 的 alias。
- seed 指向錯 axis 或錯 direction。

不同語言若 normalization 後完全相同，只有語意 binding 也完全相同才可共存。唯一刻意允許的跨 namespace 多義，是相同 canonical id 的 axis / region 雙重角色，例如 highlights / shadows；slot extractor 會保留 ambiguity，交由 scope resolver 判定，判定不了就拒絕。

新增語言後必須補：

- normalization / raw-span round-trip。
- word-boundary 與無空格語言。
- 該語言、既有語言與混用的 semantic IR 等價測試。
- negation、guard、numeric、region 與真正歧義的 reject controls。

## 3. `synthetic_axis` 證明 parser core 不需修改

測試中的 `synthetic_axis` 不是正式 OpenCV 參數，而是 extension contract：

1. 測試建立一份 isolated registry source，提供：
   - parameter spec；
   - `AxisPolicy`；
   - `RenderCapability(engine="semantic_test", ...)`；
   - 英文 / 中文 aliases；
   - 正反向 seeds。
2. 透過 `build_parameter_registry()` 建立 immutable registry。
3. 透過 `DEFAULT_PARAMETER_REGISTRY.extend(axes=...)` 注入 axis。
4. 把自訂 registry 傳給既有 extractor、scope resolver 與 assembler。
5. 驗證正反向、region、自然語序與 compound 都產生 `synthetic_axis` operation。

證據分散在三個邊界測試：

- `test_semantic_slot_extractor.py`：alias → slot，不改 extractor。
- `test_semantic_scope_resolver.py`：synthetic axis 與既有 axis 共用 scope，不加 axis branch。
- `test_semantic_operation_assembler.py`：組成同一 language-neutral IR，不加 axis branch。

`test_semantic_registry.py` 另驗證缺 schema、policy、render capability、alias、seed，或 numeric parity 漂移時會直接失敗。

`semantic_test` 是刻意未知的測試 engine，所以可用虛擬 capability 驗證語意擴充；它不代表已有影像效果。若同一 synthetic axis 假稱 `engine="opencv"`，registry 必須因沒有正式 public pixel handler 而拒絕。這個負向測試防止「parser 接受了，但 renderer 根本不會改像素」。

執行 blocker：

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' -m unittest `
  tests.test_semantic_registry.ParameterRegistryExtensionTests `
  tests.test_semantic_slot_extractor `
  tests.test_semantic_scope_resolver `
  tests.test_semantic_operation_assembler
```

新增未來 axis 時，若必須修改 `semantic_slot_extractor.py`、`semantic_scope_resolver.py` 或 `semantic_operation_assembler.py` 中的 axis 名稱判斷才通過，extension contract 就算失敗。

## 4. 新增正式影像參數的集中擴充點

正式參數比 vocabulary-only 擴充多一層「可執行能力」契約。請依順序完成，讓每一層能 fail fast。

### 4.1 `edit_schema.py`

在 `EDIT_PARAMETER_SPECS` 新增唯一 key，完整提供：

- `label`、`label_en`、`group`；
- `minimum`、`maximum`、`step`、`neutral`、`unit`；
- `default_visible`；
- `public`、`manual_adjustable`、`semantic_enabled`。

`PUBLIC_PARAMETER_KEYS`、`MANUAL_PARAMETER_KEYS`、`SEMANTIC_PARAMETER_KEYS` 由 flags 衍生，不應再手寫第二份清單。

目前 v1 的 default semantic registry 以 `MANUAL_PARAMETER_KEYS` 建立，而且 OpenCV render contract 要求 public pixel handlers 與 manual keys 完全相同。因此正式、公開、可語意控制的 OpenCV axis 應同步成為 manual axis；若未來要支援「semantic-only、不可手動」參數，必須先明確改版這個跨層契約，不能只切換一個 flag 讓各層集合漂移。

### 4.2 `adaptive_policy.py`

在 `AXIS_POLICIES` 依 schema 順序加入 `_policy(...)`：

- `family`：`signed`、`ratio` 或 `one_sided_amount`。
- `transform`：`linear` 或 `log`。
- 全域唯一的 `positive_intent` / `negative_intent`。
- `subtle`、`normal`、`strong` 三個正向及反向 seed。
- log 軸若 range 含非正值，要提供安全的 `minimum_active`。

policy 的 range、neutral、quantum 必須與 schema 完全一致；seed 必須 finite、在 range 內、方向正確且嚴格單調。`ADAPTIVE_AXIS_ORDER`、policy keys 與 manual schema order 會做 exact parity 檢查。

### 4.3 `semantic_registry.py`

為新 axis 補：

- `DEFAULT_AXIS_ALIASES[axis]`；
- `DEFAULT_TEST_SEEDS[axis]`；
- 可安全渲染的 `RenderCapability`。

default OpenCV capability 由 `MANUAL_PARAMETER_KEYS` 衍生，parameter key 在 OpenCV v1 必須與 semantic axis id 相同。所有 axis-indexed sources 必須 exact-key parity；缺一層或多一個未宣告 key都應在 registry 建立時失敗。

### 4.4 `render_contract.py`

把新 key 加入 `OPENCV_PUBLIC_PIXEL_HANDLERS`，但只能在實際 handler 同一變更中加入。

`validate_builtin_render_contracts()` 會檢查：

- public pixel handlers 與 manual schema 完全一致；
- public + internal handlers 與全部 schema keys 完全一致；
- region / mask contract 與 edit schema 完全一致。

不要用 contract 宣告尚未實作的能力。

### 4.5 `opencv_processor.py`

同時完成：

1. 在 `DEFAULT_OPENCV_PARAMETERS` 加入 key；若沒有明確 baseline 理由，預設應使用 neutral。
2. 實作純 OpenCV / NumPy pixel handler。
3. 在 `create_opencv_result()` 的明確處理順序中呼叫 handler。
4. 確認 neutral input 不改像素，正反方向或 one-sided amount 符合 policy。
5. 確認局部 region 是先算完整 adjusted image，再只經合法 mask blending；不得繞過 `_apply_region_mask()`。

`DEFAULT_OPENCV_PARAMETERS` 必須與 render contract 的 public + internal keys 完全一致。

### 4.6 `opencv_parameter_mapper.py`

在 `NEUTRAL_OPENCV_PARAMETERS` 加入新 key，確保 raw / adaptive render vector 與 reset 有完整 neutral 基準。

registry-driven adaptive operation 由 policy 產生 render value，不需要新增 prompt template。只有產品明確要求舊式 preset 或 legacy intent 也驅動此參數時，才更新 `_OPENCV_TEMPLATE_ADJUSTMENTS` / `_OPENCV_PRESET_ADJUSTMENTS`；不要為了新 axis 回頭增加完整 prompt mapping。

### 4.7 Flutter metadata

後端的 `manual_parameter_schema()` 會公開 label、雙語 labels、group、range、step、neutral、unit、visibility 與 order；adaptive history 的 `policy_registry_payload()` 另公開 transform、quantum 與 policy metadata。

Flutter 的 `ParameterMetadataCatalog.fromSources()` 會合併：

1. manual schema：顯示名稱、unit、neutral、range、step。
2. adaptive `policy_registry`：transform 與 adaptive metadata。
3. 舊版 fallback：只供舊 history / 缺 metadata 相容。

正式新增 axis 不應再建立新的 `_adaptivePublicAxes`、`parameterLabels`、neutral、unit 或 transform 常數。應驗證伺服器 metadata 就能讓新 key 顯示、格式化、reset 與摘要。只有確定需要支援「完全沒有 schema metadata 的舊 history」時，才審慎補 legacy fallback。

### 4.8 正式參數最低測試

- schema flags、labels、range、neutral、step 與 policy exact parity。
- positive / negative / subtle / normal / strong policy seed。
- alias、language equivalence、region、numeric、reset、pair、triple、four-axis reject。
- OpenCV neutral 不改像素。
- 新 public capability 從 neutral 調到代表值時，至少一個像素確實改變。
- global 與所有宣告支援的 local region。
- manual preview 不寫 history、commit 只寫一筆。
- prompt route 只 render 一次、只寫一筆 history。
- Flutter 以 fake 未知 key metadata 驗證 label、unit、neutral、transform，不依賴 Dart axis 常數。

## 5. 安全契約

### 5.1 Collision

alias 必須以 runtime normalization 後的結果審核。不要用 punctuation、全半形、大小寫或 language tag 掩蓋語意衝突。真正的 axis / region 多義必須保留成 ambiguous slot，由 scope 解決；不能靠 alias priority 靜默選一個結果。

### 5.2 Region 與 mask

只替現有 region 加翻譯時，不需改 mask。

若新增正式 region，必須一起更新：

- `EDIT_REGION_MASK_TYPES` 的唯一 region → mask mapping；
- `DEFAULT_REGIONS` aliases；
- render contract；
- `_build_region_mask()` 的實際 handler，或 semantic mask service 的合法 target；
- target found / not found、cache、history 與 pixel-locality 測試。

`require_region_mask_pair()` 要求精確配對。已驗證的 local edit 若沒有 mask，processor 必須丟錯；不可 fallback 到 `all`。人物 / 背景找不到可靠人物時仍是 `semantic_target_not_found`，不是 parser failure。

### 5.3 Numeric

- 所有 schema / policy / seed / LLM numeric 必須 finite。
- absolute value 必須落在 axis schema range。
- relative delta 不可超過整個 axis range width。
- unsigned delta 需要明確 relative relation；signed value 與 direction evidence 不可衝突。
- `NaN`、`Infinity`、非法 decimal、未定義百分比或缺 relation 必須拒絕。
- manual API 嚴格檢查 range 與 step，不靜默 clamp。
- semantic validator 不應靠 renderer clamp 掩蓋錯誤。

### 5.4 Grounded LLM

deterministic slots 已完整時，LLM 不得被呼叫或覆蓋結果。候選只可輸出 registry 中的 semantic axis / region / operation：

- 每個 semantic 欄位必須附原文 exact evidence span。
- 最多 3 個 operation，且同一 region。
- confidence 必須通過門檻。
- 不得輸出 `parameters`、`engine_parameters`、processor 或 render values。
- unknown axis、錯方向、缺 evidence、額外欄位、hallucinated compound、timeout、exception、empty 或 malformed 都回結構化錯誤，不套用部分結果。

目前 `semantic_shadow_mode.py` 是 opt-in observer。它只比較 deterministic / candidate parity，資料有深度、項目數與文字長度上限；不回饋 compiler、controller、renderer 或 history。新增 alias / language 後要重跑 shadow tests，確認 production bytes、engine parameters 與 history 都不受 observer 影響。

### 5.5 Fail closed 與原子性

以下情況不可部分套用：

- unresolved semantic residue；
- negation / guard / `or`；
- 同軸矛盾或重複 operation；
- 超過 3 個 axis；
- 多 region 或 region scope 不唯一；
- unknown axis / region / operation；
- numeric 不合法；
- LLM 與 deterministic evidence 衝突；
- local mask 不存在或 target 找不到。

route 的 `409 / 422` 必須維持不 render、不新增 history、不建立 result / task orphan、不改 selected parent。合法 compound 則只能 render 一次並新增一筆 history。

### 5.6 已知 compatibility gap 與 bare descriptor 歧義

目前 negated-context 與 overexposure outcome guard 會安全地回傳
結構化 `422`，且維持原子性；部分舊測試原先期待它們成功，因此這是
「安全保留、legacy success contract 尚待裁決」的 compatibility gap，
不是 unsafe acceptance。不可為了讓舊測試轉綠，就把 guard、否定或
結果限制詞降級成 `noise`。

無明確動作的 bare descriptor 也仍有產品語意待裁決。目前觀察到：

- `很暗`：按 observation 解讀，產生 brightness `+10`；
- `非常暗`：按 strong macro 解讀，產生 brightness `-30`；
- `very dark`：按 strong macro 解讀，產生 brightness `-30`。

這三者在字面等價與跨語言方向上不一致，不能宣稱已解決。人工驗收若
要得到可預期控制，暫時使用帶明確動作的說法，例如「暗很多」、
`much darker` 或 `make it very dark`。正式修正應先決定 bare
descriptor 的產品政策，再用可跨 axis、跨語言驗證的 typed rule
實作；不要逐句加例外。

### 5.7 Axis-neutral observation → remedy

「飽和度太高，降一點」不再是待修句型。現在由通用 typed
composition 處理：

1. observation 先依 registry evidence 唯一綁定 axis 與目前狀態方向；
2. remedy 必須提供可驗證的修正方向；
3. assembler / validator 檢查 remedy 是否確實修正 observation，而
   不是加劇它；
4. region scope 走既有 generic resolver，不為 saturation 或 `sky`
   增加專用分支。

Focused regression 已覆蓋中文、英文、多個 axes 與 `sky` 局部正例；
目前結果為 **4 / 4 PASS**。若 remedy 與 observation 方向相反，
整句回 structured `422`，不得局部套用。新增語言或 axis 時，應補
observation alias 與 paired positive / opposite-remedy controls，
不應新增完整句 mapping。

## 6. 測試順序與完成判準

### 6.0 Parser-safety 歷史基線與目前 production regression

凡是修改 axis aliases、shared concepts、typed relations、scope 或
validator，先重跑 parser-safety suite：

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' -m unittest `
  tests.test_semantic_registry `
  tests.test_semantic_alias_audit `
  tests.test_semantic_architecture_audit `
  tests.test_semantic_slot_extractor `
  tests.test_semantic_scope_resolver `
  tests.test_semantic_operation_assembler `
  tests.test_semantic_validator `
  tests.test_semantic_parser `
  tests.test_semantic_generated_properties `
  tests.test_semantic_holdout
```

**241 / 241 passed** 是 takeover 前完成的歷史 parser-safety
baseline，不是目前 production 全回歸的替代品。最新 suite 為
**324 / 326 PASS**；2 個 FAIL 只發生在 generated frozen metadata
計數（787 vs 舊 expected 789、directional aliases 157 vs 舊
expected 159），目前沒有 runtime semantic mismatch。即使如此，
current full suite 仍不是全綠。2026-07-26 production 證據、compact
legacy gap、Flutter runner 狀態與人工待驗項目，以
`docs/semantic_full_regression_20260726.md` 為準。Frozen holdout
目前為 **363 / 363**；新增 registry 資料後案例總數可以合理變動，
但 frozen metadata 只能在確認 registry coverage 變化符合預期後
更新，不得為了轉綠而刪減 coverage、改寫 holdout semantic expected
或容許 unsafe acceptance。

### 6.1 語意核心

```powershell
Set-Location 'C:\AI_Photo_Code\AI_photo_editor\backend'

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' -m unittest `
  tests.test_semantic_registry `
  tests.test_semantic_normalizer `
  tests.test_semantic_slot_extractor `
  tests.test_semantic_scope_resolver `
  tests.test_semantic_operation_assembler `
  tests.test_semantic_validator `
  tests.test_llm_semantic_adapter `
  tests.test_semantic_shadow_mode `
  tests.test_semantic_parser
```

### 6.2 生成式、holdout 與效能

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  tests/run_semantic_generated_report.py

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m unittest tests.test_semantic_generated_properties tests.test_semantic_holdout

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  tests/run_semantic_performance_release.py --samples 1000
```

完成判準：

- generated report `failed = 0`，固定 seed / manifest signature 的變更可解釋。
- holdout 363 案全部符合 canonical IR 或預期 reject，`unsafe_acceptance = 0`。
- deterministic normal prompt p95 ≤ 10 ms。
- 2,000 code-point 合法與對抗 prompt p95 ≤ 120 ms，且無非線性爆增。

2026-07-26 最新實測：normal p95 **2.636625 ms**，PASS；2,000
code-point legal / adversarial p95 分別為 **133.319 ms** /
**173.975 ms**，超過 120 ms。依目前產品政策，本階段將結果記錄為
已知 performance gap，不為此偏離語意主線深挖。

### 6.3 Route、controller、history、mask 與 pixel

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' -m unittest `
  tests.test_semantic_adaptive_adapter `
  tests.test_semantic_compiler_integration `
  tests.test_semantic_route_integration `
  tests.test_english_route_history_parity `
  tests.test_adaptive_v2_route `
  tests.test_edit_schema `
  tests.test_adaptive_policy_contract `
  tests.test_manual_edit_service `
  tests.test_opencv_processor
```

真實 SegFormer / API：

```powershell
$env:AI_PHOTO_RUN_SEGMENTATION_INTEGRATION = '1'
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m unittest tests.test_semantic_api_integration
Remove-Item Env:AI_PHOTO_RUN_SEGMENTATION_INTEGRATION
```

### 6.4 完整 backend 與靜態檢查

```powershell
& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m unittest discover -s tests

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m compileall app tests

& 'C:\AI_Photo_Code\AI_photo_editor\backend\.venv\Scripts\python.exe' `
  -m pip check
```

### 6.5 Flutter metadata 與 UI

Windows 中文使用者路徑若讓 Flutter runner 卡在 loading，先把本次 shell 的 TEMP / TMP 指向純英文、可寫入目錄：

```powershell
$semanticFlutterTemp = 'C:\AI_Photo_Code\AI_photo_editor\.tmp\flutter_semantic'
New-Item -ItemType Directory -Force -Path $semanticFlutterTemp | Out-Null
$env:TEMP = $semanticFlutterTemp
$env:TMP = $semanticFlutterTemp
Set-Location 'C:\AI_Photo_Code\AI_photo_editor\frontend'

flutter test --no-pub -r expanded
flutter analyze --no-pub
flutter build web --no-pub
dart format --output=none --set-exit-if-changed lib test
```

至少保留 `synthetic_axis` Flutter fake metadata 測試，證明陌生 axis 的 label、unit、neutral、transform、摘要與 reset 不需修改 Dart parser / constants。

2026-07-26 在 Codex sandbox 內重跑 Flutter 指令於 300 秒 timeout，
沒有取得 assertion 或 analyzer 結果，因此該次既不是 PASS 也不是
FAIL。目前最後一份已確認 Flutter 證據仍是 2026-07-25：完整測試
**56 / 56**、metadata **11 / 11**、controller **16 / 16**，
analyzer 與 Web build 均 PASS。這些是既有版本證據，不可寫成
2026-07-26 current-source rerun；本次來源仍需在一般 PowerShell
terminal 依上列方式補跑。

### 6.6 最終工作樹檢查

```powershell
Set-Location 'C:\AI_Photo_Code\AI_photo_editor'
git diff --check
git status --short
```

確認沒有修改 `langchain_model/`、`model_v2/`、`dataset_pilot/`，並在 commit 前另外處理目前 `.gitignore` 會忽略 `backend/tests/` 的既有問題。未經使用者確認不要 commit；未經使用者同意不要 push。

## 7. Code review 禁止清單

出現以下任一項，語意擴充不得通過 review：

- 在 parser core 加入完整句 regex。
- `if axis == "brightness"`、`if "saturated" in prompt` 等 axis / prompt-specific 分支。
- 為新語言複製一套 extractor、scope resolver 或 assembler。
- 把 holdout 失敗 prompt 整句加入 alias 或 noise。
- 用 language tag、大小寫、dash 或全半形迴避 collision。
- 讓 LLM 直接輸出 OpenCV parameters 或跳過 evidence / validator。
- renderer 靜默 clamp semantic 錯誤。
- local mask 失敗後改成全圖。
- 新增 schema key但沒有 policy、真實 pixel handler、前端 metadata 與測試。
- 只驗證 HTTP 200，沒有比對 IR、history、render vector、mask 與像素。

通過標準不是「某一句現在不報錯」，而是新增資料後，所有 axis、語言、region、numeric、compound 與安全不變量仍由同一套通用核心成立。
