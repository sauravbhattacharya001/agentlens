# Changelog

All notable changes to AgentLens will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0](https://github.com/sauravbhattacharya001/agentlens/compare/v1.0.0...v1.1.0) (2026-04-28)


### Features

* activity heatmap — day-of-week × hour-of-day matrix ([cdcb718](https://github.com/sauravbhattacharya001/agentlens/commit/cdcb7185ed330e1b65b9301a9b9f2870ca7fdaa4))
* add agent leaderboard API endpoint ([850ca6d](https://github.com/sauravbhattacharya001/agentlens/commit/850ca6d6ed1cce75ab24b10b60c36127f6e13aca))
* add alert rules — threshold-based alerting for agent observability ([1068275](https://github.com/sauravbhattacharya001/agentlens/commit/1068275b17655556c53229bc3fbf5d4a9ef5ced2))
* add alert rules engine with conditions, rules, and engine ([de0c2f0](https://github.com/sauravbhattacharya001/agentlens/commit/de0c2f0f8781e97b56467a664a5e37cb2902d514))
* add ArticleReadLaterReminder -- smart reminders for saved articles ([a3fd040](https://github.com/sauravbhattacharya001/agentlens/commit/a3fd040236dfd932075234f70c3dc16ef326154e))
* add CLI audit command for agent action audit trail ([2544705](https://github.com/sauravbhattacharya001/agentlens/commit/25447057d4a86db745a9068a9fb0c189793a1cbe))
* add client-side AlertRule, AlertManager, and MetricAggregator ([72cb9e8](https://github.com/sauravbhattacharya001/agentlens/commit/72cb9e8d0963fbdd30b29137bc7e9c068694d2ea))
* add Codecov integration for coverage tracking ([a18f8e1](https://github.com/sauravbhattacharya001/agentlens/commit/a18f8e17c1208f2360f40d470fad724cda334cee))
* add Command Center dashboard — unified activity feed ([7c29141](https://github.com/sauravbhattacharya001/agentlens/commit/7c2914107362708ec0375118d83604d360043245))
* add Compliance Checker for policy-based session validation ([fdb305e](https://github.com/sauravbhattacharya001/agentlens/commit/fdb305e427d4b9e3f7fdb0d0abfa668e47ea46e0))
* add CostForecaster — predict future AI costs from historical usage ([62dfb76](https://github.com/sauravbhattacharya001/agentlens/commit/62dfb764a05a009e887b393197c209ddef8f6f6e))
* add Data Retention & Cleanup — configurable policies, DB stats, manual purge ([fefebab](https://github.com/sauravbhattacharya001/agentlens/commit/fefebab9b23f92448c7f0e8137db1b7dab713bfc))
* add Error Analytics API (/errors endpoints) ([f7c95b3](https://github.com/sauravbhattacharya001/agentlens/commit/f7c95b3e6bb593771b89279bf45622416c81e48d))
* add Error Analytics dashboard tab ([2d33ab6](https://github.com/sauravbhattacharya001/agentlens/commit/2d33ab6f384058fd50994a0599e2c14ad5fcaf2c))
* add Forecast API — cost/usage prediction with budget alerts ([#81](https://github.com/sauravbhattacharya001/agentlens/issues/81)) ([1b513e1](https://github.com/sauravbhattacharya001/agentlens/commit/1b513e146265e4fda21063e3d75aaf129d9e7162))
* add incident postmortem generator (SDK + backend) ([5da336f](https://github.com/sauravbhattacharya001/agentlens/commit/5da336fd6324e6fdd2abbb8df02fe242447e7995))
* add lightweight schema migration system ([2669724](https://github.com/sauravbhattacharya001/agentlens/commit/26697249baa01f25fe2074afaf91852cb25477e9)), closes [#160](https://github.com/sauravbhattacharya001/agentlens/issues/160)
* add Postmortem Dashboard tab for incident reports ([34da9b8](https://github.com/sauravbhattacharya001/agentlens/commit/34da9b8aa22ba3f0fdae39c3aa3f3f44812daa1b))
* add PyPI and npm package publishing workflows ([b756916](https://github.com/sauravbhattacharya001/agentlens/commit/b75691642973fc649e33fb6c7a30491d7b3a9d17))
* add Release Please for automated versioning and releases ([a552b99](https://github.com/sauravbhattacharya001/agentlens/commit/a552b99ce206781a33869c756856637acb5114bc))
* add Response Quality Evaluator for agent output scoring ([81524c1](https://github.com/sauravbhattacharya001/agentlens/commit/81524c1654514542323996dac71f667e0879fd9f))
* add scheduled auto-correlation, SSE streaming, and deduplication ([6f29b7e](https://github.com/sauravbhattacharya001/agentlens/commit/6f29b7e7d177a1f792113e4be574fdddb9b657b6)), closes [#32](https://github.com/sauravbhattacharya001/agentlens/issues/32)
* add service dependency map for tool/API usage analysis ([32797d9](https://github.com/sauravbhattacharya001/agentlens/commit/32797d9115aa1644c85ec606e5b5bffd46082fa5))
* add Session Annotations for observability notes and collaboration ([ad8392f](https://github.com/sauravbhattacharya001/agentlens/commit/ad8392f82927417b0c361de2930d9553a0205013))
* add session bookmarks — star/unstar sessions and filter by bookmarks ([ecd7258](https://github.com/sauravbhattacharya001/agentlens/commit/ecd72588f318bb4aff52b0b97fe31e9cc749e544))
* add Session Correlation Engine (correlation.py) ([#52](https://github.com/sauravbhattacharya001/agentlens/issues/52)) ([23aab77](https://github.com/sauravbhattacharya001/agentlens/commit/23aab774c05d138d40e3fbfc54ea7d7228a62905))
* add Session Diff Viewer — interactive dashboard page for comparing two agent sessions ([710db07](https://github.com/sauravbhattacharya001/agentlens/commit/710db07d517eb8791cb76d2d05d3cda15740254f))
* add Session Replay Player for step-by-step event playback ([#78](https://github.com/sauravbhattacharya001/agentlens/issues/78)) ([7d02fe3](https://github.com/sauravbhattacharya001/agentlens/commit/7d02fe30c294516bfb5a3dfc530ff2d3b0a55091))
* add Session Timeline Renderer (TimelineRenderer class) ([566e0c8](https://github.com/sauravbhattacharya001/agentlens/commit/566e0c830d201a757c95df1e0a39b50a6324a428))
* add sessions test suite (58 tests) + refactor event parsing ([6826f9e](https://github.com/sauravbhattacharya001/agentlens/commit/6826f9eccd1de5e4cbad58bb92b0fc38602e4dc9))
* add SLA Monitor for service-level compliance tracking ([16cc169](https://github.com/sauravbhattacharya001/agentlens/commit/16cc169c46f47e8902c216eca75e09cdd1896fc5))
* add Token Budget Tracker for usage limits and cost control ([530272b](https://github.com/sauravbhattacharya001/agentlens/commit/530272b3609613e84a609077a140e63dbc41cd9b))
* add Trace Correlation Rules Engine ([9248000](https://github.com/sauravbhattacharya001/agentlens/commit/9248000be48b8901f76f494b8b9127db54328b2f))
* add trace sampling and rate limiting for production deployments ([52b1b07](https://github.com/sauravbhattacharya001/agentlens/commit/52b1b071cdb8275a1479b5f2308e05f01bbeea18))
* Agent Baselines — track and check session performance against rolling averages ([a31c45a](https://github.com/sauravbhattacharya001/agentlens/commit/a31c45ad57a36db517bb5cdf5c661937a0fae463))
* Agent Communication Graph - interactive inter-agent message flow visualization ([3151b71](https://github.com/sauravbhattacharya001/agentlens/commit/3151b7144f836154a7e555722124409e42783fd2))
* Agent Mood Ring dashboard - synthesizes fleet metrics into intuitive mood visualization ([b3ec028](https://github.com/sauravbhattacharya001/agentlens/commit/b3ec0280b64120ffd7624e15810eaa1adcb31e96))
* Agent Scorecards — per-agent performance grading with composite scores, letter grades, sparkline trends, and detail drilldown ([7a74f0b](https://github.com/sauravbhattacharya001/agentlens/commit/7a74f0b5caf8880822dd5c880247d52f6c50d852))
* **analytics:** add performance percentiles API endpoint ([1713d2b](https://github.com/sauravbhattacharya001/agentlens/commit/1713d2b070884ba55ed8f85b64b24f28efb7c419))
* Anomaly Detector — statistical outlier detection for sessions ([3de45aa](https://github.com/sauravbhattacharya001/agentlens/commit/3de45aa0f07ea866e412620420c62f9548df95ff))
* behavioral drift detection module ([170d6f8](https://github.com/sauravbhattacharya001/agentlens/commit/170d6f8d1fd8d265ce35f83eac22d90de0b73be1))
* **budgets:** cost budgets API — per-agent and global spending limits ([1c412d6](https://github.com/sauravbhattacharya001/agentlens/commit/1c412d6f7e323c062fec2f91f53df8a69630fb8b))
* ChallengeReplayGuard -- HMAC-signed challenge tokens with nonce, TTL, one-time-use enforcement ([f117498](https://github.com/sauravbhattacharya001/agentlens/commit/f117498e397561635c79fb991f2af958b6b77f62))
* **cli:** add 'alert' command for rich alert management ([e665e54](https://github.com/sauravbhattacharya001/agentlens/commit/e665e544593e1679865a924e6256b2e0089fd345))
* **cli:** add 'report' command — generate time-range summary reports ([f87b377](https://github.com/sauravbhattacharya001/agentlens/commit/f87b377b028592b257a9e3e7975ca72c6a29f7d5))
* **cli:** add 'tail' command for live-following session events ([e189dcc](https://github.com/sauravbhattacharya001/agentlens/commit/e189dcc21a90fa132a1559154b2d44c3998c3d7e))
* **cli:** add 'top' command — live session leaderboard ([0fa1275](https://github.com/sauravbhattacharya001/agentlens/commit/0fa1275640670494ceff2feda22fdffd0240a323))
* **cli:** add baseline command for agent performance baselines ([5a6299f](https://github.com/sauravbhattacharya001/agentlens/commit/5a6299f7698f4b6eb8b01a280320428b742fb808))
* **cli:** add bottleneck command for performance bottleneck analysis ([1ba6f43](https://github.com/sauravbhattacharya001/agentlens/commit/1ba6f433cc2da20433c96dad9308ea719c480360))
* **cli:** add budget command for cost budget management ([2b8b10b](https://github.com/sauravbhattacharya001/agentlens/commit/2b8b10b5269a3f1b08368bd2e6c5d559f19f77f0))
* **cli:** add capacity planning command ([b740a1f](https://github.com/sauravbhattacharya001/agentlens/commit/b740a1fbe3db7fa2774caf206c8645791bc47c60))
* **cli:** add config command for persistent CLI configuration ([79c97f4](https://github.com/sauravbhattacharya001/agentlens/commit/79c97f49c05c828193f4473857c27ad86d95eec5))
* **cli:** add correlate command — pairwise metric correlation analysis ([cd59760](https://github.com/sauravbhattacharya001/agentlens/commit/cd597601f0f7ae98b6d5e53b754ce4e87913d826))
* **cli:** add dashboard command - generate self-contained HTML dashboard ([7cdafaa](https://github.com/sauravbhattacharya001/agentlens/commit/7cdafaa7c9fdf5cce89caacf3d757df7cdc7bbf3))
* **cli:** add depmap command – visualise agent-to-tool dependency graph ([39fc3b1](https://github.com/sauravbhattacharya001/agentlens/commit/39fc3b18da352b246729faca0488273735ef3645))
* **cli:** add diff command for side-by-side session comparison ([4ba138c](https://github.com/sauravbhattacharya001/agentlens/commit/4ba138c2d6931e5b4a5362901d03e8e3015f6360))
* **cli:** add digest command — periodic summary reports ([2bc0efa](https://github.com/sauravbhattacharya001/agentlens/commit/2bc0efad5891a330bc24516e924154de0e9dd459))
* **cli:** add flamegraph command — generate interactive HTML flamegraphs from sessions ([08b0733](https://github.com/sauravbhattacharya001/agentlens/commit/08b0733a0bf59c6f5d6ed948491208be52d79f90))
* **cli:** add forecast command – predict future costs/usage from historical trends ([6b69498](https://github.com/sauravbhattacharya001/agentlens/commit/6b69498fd4215fca3249a9dd2e217d76aa7a583c))
* **cli:** add funnel command - agent workflow funnel analysis ([5fa0d2d](https://github.com/sauravbhattacharya001/agentlens/commit/5fa0d2d955ffe9ec36f5b00865323bbd03134cee))
* **cli:** add gantt command – interactive HTML Gantt chart for sessions ([8bcb031](https://github.com/sauravbhattacharya001/agentlens/commit/8bcb03134482be46e5058321d7099ac9b93e1ef1))
* **cli:** add heatmap command — terminal activity heatmap (day×hour) ([dee77a1](https://github.com/sauravbhattacharya001/agentlens/commit/dee77a1120ef25db2c05bebb8f94653bdadeb15b))
* **cli:** add leaderboard command to rank agents by performance ([e010b2f](https://github.com/sauravbhattacharya001/agentlens/commit/e010b2f54ede8a8dd04518d90e67f3f2ae6b20b8))
* **cli:** add outlier command — IQR-based anomaly detection for sessions ([eca6cbf](https://github.com/sauravbhattacharya001/agentlens/commit/eca6cbf24362e99ecb0ee64cd08eee7fa3ddb40f))
* **cli:** add postmortem command — generate incident reports from terminal ([8924df6](https://github.com/sauravbhattacharya001/agentlens/commit/8924df666c72e3cbcbf4a7d546d2cf1895f202d6))
* **cli:** add profile command — agent performance profiler ([b34024f](https://github.com/sauravbhattacharya001/agentlens/commit/b34024f4e8284d695558cb6496d7caad070a06c0))
* **cli:** add replay command — terminal session playback with live streaming ([2a310d8](https://github.com/sauravbhattacharya001/agentlens/commit/2a310d84005c6dfaccf89476f4414a7e6066a1a0))
* **cli:** add retention command for data age analysis & cleanup ([b954874](https://github.com/sauravbhattacharya001/agentlens/commit/b954874de26eea24030b6ddb9bad09e3a132c397))
* **cli:** add sla command for SLA compliance evaluation ([e2216be](https://github.com/sauravbhattacharya001/agentlens/commit/e2216bed9f5d9c7260a043dda007412e2cba50b2))
* **cli:** add snapshot command for point-in-time system state capture ([5161ced](https://github.com/sauravbhattacharya001/agentlens/commit/5161ced1dd2c0789fabb439213ca6938671afabb))
* **cli:** add trace command — terminal waterfall view for session events ([d6b6569](https://github.com/sauravbhattacharya001/agentlens/commit/d6b65694f43851c6d6c9a75079129a32ad3bd522))
* **cli:** add trends command — period-over-period metric comparison with sparklines ([c6451a5](https://github.com/sauravbhattacharya001/agentlens/commit/c6451a5dd6c09eae63fc7a7b669f08cfb4585694))
* **cli:** add watch command — real-time streaming metric dashboard ([349cc8f](https://github.com/sauravbhattacharya001/agentlens/commit/349cc8fcd3a5ff4490486397efb7f03dbee3dd40))
* Cost Analytics — aggregate cost breakdown by model + daily trend ([#76](https://github.com/sauravbhattacharya001/agentlens/issues/76)) ([963583e](https://github.com/sauravbhattacharya001/agentlens/commit/963583ed40def56a1b1d148aedd646a3c8e8227b))
* CostOptimizer — intelligent model selection recommendations (48 tests) ([d82db15](https://github.com/sauravbhattacharya001/agentlens/commit/d82db15777f953e3dab2efdd6726c8dc7f622956))
* **dashboard:** add Agent Canary Deployer ([a24e199](https://github.com/sauravbhattacharya001/agentlens/commit/a24e199a9e2d3d4e4dd4a9bb3af701ff73c88777))
* **dashboard:** add Agent Experiment Lab - A/B testing with statistical significance ([b3d654d](https://github.com/sauravbhattacharya001/agentlens/commit/b3d654d8a786f0cfe40f14be29c9e0bbc8452b93))
* **dashboard:** add Agent Regression Tracker ([71be453](https://github.com/sauravbhattacharya001/agentlens/commit/71be453ef4117f041ffd34815ce0415efa0fcaa6))
* **dashboard:** add Alert Rules Builder page ([afbab9e](https://github.com/sauravbhattacharya001/agentlens/commit/afbab9e74f2dfcf672331307a7e8a596bd487f21))
* **dashboard:** add Annotations Panel to session detail view ([3384a64](https://github.com/sauravbhattacharya001/agentlens/commit/3384a64803072aa3c5b3edeff34a82bdddc9162d))
* **dashboard:** add Compliance Auditor - interactive policy-based agent audit with 12 configurable policies, severity scoring, compliance gauge, violation timeline, proactive insights, auto-audit mode, filtering, and multi-format export (JSON/CSV/HTML) ([110c0b0](https://github.com/sauravbhattacharya001/agentlens/commit/110c0b08116b34e42893ab4dbe25335da1df5f45))
* **dashboard:** add light/dark theme toggle ([e33fb0d](https://github.com/sauravbhattacharya001/agentlens/commit/e33fb0dc87367a3ba033756626962b20eabf3a99))
* **dashboard:** add Session Replay Debugger page ([21c547f](https://github.com/sauravbhattacharya001/agentlens/commit/21c547fe365ef1158051a35303ba412bc38f49f9))
* **dashboard:** add What-If Scenario Planner ([46473b3](https://github.com/sauravbhattacharya001/agentlens/commit/46473b35c14ed1a6234253f2f164702f94443ca0))
* **dashboard:** Command Palette (Ctrl+K) ([4205680](https://github.com/sauravbhattacharya001/agentlens/commit/42056808c65ee0b74d35f2d0c2abe6b87f588567))
* **dashboard:** Cost Anomaly Detector — proactive cost monitoring ([922558a](https://github.com/sauravbhattacharya001/agentlens/commit/922558a7b5165558a396efa7b1d2b3fe0987ab69))
* **dashboard:** Error Analytics Dashboard ([78276d7](https://github.com/sauravbhattacharya001/agentlens/commit/78276d7adaa9170cf35030875cd72d77c1fa04bd))
* **dashboard:** Session Timeline — interactive Gantt chart with concurrency analysis ([8e6828e](https://github.com/sauravbhattacharya001/agentlens/commit/8e6828e253cbfb94b69b315459c11bffeee1dbc4))
* **dashboard:** SLA Compliance Dashboard — interactive page for monitoring SLA targets, compliance rings, violation alerts, and compliance history bar charts ([a2aad8a](https://github.com/sauravbhattacharya001/agentlens/commit/a2aad8aa1dfec52c4ea5b1e0a01ae85f0b7a1150))
* **dashboard:** Smart Alert Correlator — groups related alerts to reduce noise ([592df55](https://github.com/sauravbhattacharya001/agentlens/commit/592df55d3b834a37d1360fce9076ff2294b66a3d))
* **dashboard:** Smart Triage Queue - autonomous incident prioritization with auto-scoring, escalation, and proactive recommendations ([a27b10b](https://github.com/sauravbhattacharya001/agentlens/commit/a27b10bdc2f22f6d237b733133179929f070654a))
* **docs:** add client-side search with Ctrl+K shortcut ([3925edb](https://github.com/sauravbhattacharya001/agentlens/commit/3925edb9432893d7172cff6d0fbc72b799f73bb4))
* Error Fingerprinting — automatic error grouping and tracking ([#69](https://github.com/sauravbhattacharya001/agentlens/issues/69)) ([c49cf91](https://github.com/sauravbhattacharya001/agentlens/commit/c49cf9155f01d3878715282fb8389abb8ccd7806))
* event search & filter — full-text search, type/model/token/duration filtering in timeline ([abf1de7](https://github.com/sauravbhattacharya001/agentlens/commit/abf1de79ebd1eb411e6309e6139a561ca43fc58c))
* interactive session flamegraph visualization ([#83](https://github.com/sauravbhattacharya001/agentlens/issues/83)) ([9502631](https://github.com/sauravbhattacharya001/agentlens/commit/95026319df54a547daa6e69b189df364d05ad168))
* Leaderboard Panel — dashboard UI for agent rankings ([#74](https://github.com/sauravbhattacharya001/agentlens/issues/74)) ([8dd5f75](https://github.com/sauravbhattacharya001/agentlens/commit/8dd5f75b506861d0369bf54442be1d8a3d17436f))
* **profiler:** add Agent Behavior Profiler with drift detection ([7d94fbf](https://github.com/sauravbhattacharya001/agentlens/commit/7d94fbf1c42700305d5b6381e5ec6d4934610c12))
* RetryTracker -- retry chain analysis, tax computation, storm detection ([#54](https://github.com/sauravbhattacharya001/agentlens/issues/54)) ([8e203d3](https://github.com/sauravbhattacharya001/agentlens/commit/8e203d3b06411dd1ae70ab08960b64ab6ddf470c))
* **sdk:** A/B Test Analyzer — experiment framework for comparing agent variants ([ef5a291](https://github.com/sauravbhattacharya001/agentlens/commit/ef5a291d2a260c1a453615756832db38d2512fe9))
* **sdk:** add CapacityPlanner -- fleet capacity planning engine ([3fdd4fa](https://github.com/sauravbhattacharya001/agentlens/commit/3fdd4faa403edf9006334bfbd2ded1c965d58170))
* **sdk:** add CLI scatter command for terminal scatter plots ([56dd134](https://github.com/sauravbhattacharya001/agentlens/commit/56dd13451f7e60d92bc50b30b715931a8371f376))
* **sdk:** add CLI tool for querying AgentLens backend ([b177387](https://github.com/sauravbhattacharya001/agentlens/commit/b177387aca9f4ec1ee8720cab430f429e5de5423))
* **sdk:** add LatencyProfiler -- per-step pipeline latency tracking, percentile baselines, slow step detection, session comparison, fleet summary (37 tests) ([66ad91e](https://github.com/sauravbhattacharya001/agentlens/commit/66ad91e49fc35327aa64c8b03a9d2dbe28f328f6))
* **sdk:** add Session Autopsy — autonomous multi-engine root-cause investigation ([7a8e06d](https://github.com/sauravbhattacharya001/agentlens/commit/7a8e06dad19bb597a2e4dbe92c8cb946b53bd7db))
* **sdk:** add Session Health Scoring module ([7f4f7b1](https://github.com/sauravbhattacharya001/agentlens/commit/7f4f7b1d824644ec95ab9268409d2e3b3d435f12))
* **sdk:** add Span context manager for grouping events into logical units ([99c5661](https://github.com/sauravbhattacharya001/agentlens/commit/99c566169b1ab4485446314bd73a93955ae259a4))
* **sdk:** PromptVersionTracker — prompt template versioning with performance correlation ([bcba3f2](https://github.com/sauravbhattacharya001/agentlens/commit/bcba3f2cbbc6ca6a60e43bb669dde1f697631c37))
* **sdk:** RateLimiter — sliding-window rate limiting for LLM API calls ([d0cbb64](https://github.com/sauravbhattacharya001/agentlens/commit/d0cbb641275923645a36bff4dafd19e3fd6cfa0e))
* **sdk:** Session Diff — structured comparison of two agent sessions ([77947ba](https://github.com/sauravbhattacharya001/agentlens/commit/77947ba70f1bd00e1f32adf39d3247b53bdd586e))
* **sdk:** SessionExporter — offline export to JSON, CSV, and standalone HTML reports ([366ca6d](https://github.com/sauravbhattacharya001/agentlens/commit/366ca6d19dc81006cac78eff5e6df347fcab4439))
* **sdk:** SessionGroupAnalyzer — group & compare sessions by agent/model/status/metadata/time ([5db9b37](https://github.com/sauravbhattacharya001/agentlens/commit/5db9b3707b516978788694a65655a6b6d6e6c784))
* **sdk:** Token Usage Heatmap — calendar-style visualization of token consumption ([078d71b](https://github.com/sauravbhattacharya001/agentlens/commit/078d71b95b348d6079bd2f502b45f09ed6aee360))
* **sdk:** Usage Quota Manager — organizational quota management ([7288ace](https://github.com/sauravbhattacharya001/agentlens/commit/7288ace10b5323424734483fedff9caa41b65ddf))
* Session Guardrails — constraint validation for agent sessions ([2e17f3f](https://github.com/sauravbhattacharya001/agentlens/commit/2e17f3f7f7e048cd294dc10d4b6afbe5ca121a29))
* Session Narrative Generator — auto-generate human-readable session summaries ([2c57ec1](https://github.com/sauravbhattacharya001/agentlens/commit/2c57ec1268eca8ad8fc7cad9ad23448f1e61d2f1))
* Session Search — full-text search, filter, and sort sessions ([4e04e9d](https://github.com/sauravbhattacharya001/agentlens/commit/4e04e9dde32f0a5af4631e4a194e4922069c5ab8))
* session tags — label, filter, and organize agent sessions ([68408da](https://github.com/sauravbhattacharya001/agentlens/commit/68408da03eba07318058441d099b5d178c29d2a2))
* SessionReplayer -- step-by-step session replay for debugging agent runs ([4d6ccd3](https://github.com/sauravbhattacharya001/agentlens/commit/4d6ccd3a6e444a4dfa8d06a1885b23209abc324b))
* SLA Monitoring -- per-agent SLA targets with compliance tracking ([6464df4](https://github.com/sauravbhattacharya001/agentlens/commit/6464df428904c70837fa77b3e113f88e8ddc0abd))
* Trace Waterfall Viewer — interactive gantt-style event visualization ([8fc27af](https://github.com/sauravbhattacharya001/agentlens/commit/8fc27af008bacbcaa62afb49c70c2310cd39cd24))
* **triage:** add auto-triage engine — unified session diagnostics with prioritized findings and remediations ([e7e047f](https://github.com/sauravbhattacharya001/agentlens/commit/e7e047fa4bb45bee5e8409701027a8d2cb77d25d))
* webhook notifications for alert rules ([80531c3](https://github.com/sauravbhattacharya001/agentlens/commit/80531c3f0882909f7644fd648994a9b07bf7028c))


### Bug Fixes

* accept all 2xx status codes as successful responses in Transport ([7c85f31](https://github.com/sauravbhattacharya001/agentlens/commit/7c85f311aa2cd9fee0dfec117bda1539ef9aa71a))
* accept all 2xx status codes as successful responses in Transport ([fdecd62](https://github.com/sauravbhattacharya001/agentlens/commit/fdecd62047972a88992da1cc569415d43494a7fb))
* add input validation to anomaly endpoints and fix profiler column name bug ([4a0c4c4](https://github.com/sauravbhattacharya001/agentlens/commit/4a0c4c4ec0d485643cc501e26a7c451513ac6fe2))
* add rate limiting and auth middleware to /correlations routes ([27eb168](https://github.com/sauravbhattacharya001/agentlens/commit/27eb1681f55838dee25b1a738c31608ee7763c91))
* add session ID validation to postmortem endpoints ([52ac652](https://github.com/sauravbhattacharya001/agentlens/commit/52ac652b401783eaf7aa476ea776cddb727d3ec3))
* add session ID validation to postmortem endpoints ([c46fd79](https://github.com/sauravbhattacharya001/agentlens/commit/c46fd798562107abfeb81867825f46880d1a6df2))
* **alert_rules:** cap event buffer and alert history to prevent memory leak ([54055a1](https://github.com/sauravbhattacharya001/agentlens/commit/54055a143e5b969bb8043dc6be788bb9a5dbde79))
* AlertManager.evaluate() cooldown race condition ([f616748](https://github.com/sauravbhattacharya001/agentlens/commit/f6167488d29333cc70ac97e63c41c16ca143d553))
* **alerts:** include agent_error and tool_error in error_rate metric ([75cc3ae](https://github.com/sauravbhattacharya001/agentlens/commit/75cc3aefe4e1a15283ca4663566de0c5759cb108))
* block IPv6 ULA/link-local and CGNAT in webhook URL validation ([#112](https://github.com/sauravbhattacharya001/agentlens/issues/112)) ([51969c6](https://github.com/sauravbhattacharya001/agentlens/commit/51969c6ad5d890efd21166bd7de10a8bb2dfa4d4))
* BudgetTracker session_index collision with multiple budgets — closes [#35](https://github.com/sauravbhattacharya001/agentlens/issues/35) ([efa509b](https://github.com/sauravbhattacharya001/agentlens/commit/efa509ba535b53597c66d48a8da2288f8adcc033))
* **budget:** update MODEL_PRICING with current models and add runtime override API ([f72537f](https://github.com/sauravbhattacharya001/agentlens/commit/f72537ffdab0ac51d3223ea6eec12e44c76e5e8c))
* CapacityPlanner crashes with ZeroDivisionError when max_error_threshold=0 ([#82](https://github.com/sauravbhattacharya001/agentlens/issues/82)) ([ef8ac50](https://github.com/sauravbhattacharya001/agentlens/commit/ef8ac50b89b651d670849b396f9f6cc1f66a1048))
* case-insensitive tool matching in compliance checker + replayer remove_filter bug ([f0e017c](https://github.com/sauravbhattacharya001/agentlens/commit/f0e017ccd0f34519b510f73a079b9821d6064ef2))
* catch JSON.parse errors for corrupted token metadata in replay guard ([80921fe](https://github.com/sauravbhattacharya001/agentlens/commit/80921feefd7f2a5d494697c832c0522eb4ed3a2b))
* catch JSON.parse errors for corrupted token metadata in replay guard ([9c7eef7](https://github.com/sauravbhattacharya001/agentlens/commit/9c7eef7fc8b3d48c131f54db66661c817bdf0d09))
* convert db and webhook tests from node:test to Jest ([a17d4c6](https://github.com/sauravbhattacharya001/agentlens/commit/a17d4c6c91691a41c4e4eb27533b0ad990928a67))
* correct daily session count aggregation in forecast ([0c33c7a](https://github.com/sauravbhattacharya001/agentlens/commit/0c33c7a4e0ed626febbff37adb473df09f5f9ce3))
* correlateByErrorCascade misses agent_error and tool_error event types ([efdaa84](https://github.com/sauravbhattacharya001/agentlens/commit/efdaa84585e041c2f14eb422e97b57d939d164d8))
* csvEscape negative number corruption and causal chain default field ([#77](https://github.com/sauravbhattacharya001/agentlens/issues/77)) ([5f587e4](https://github.com/sauravbhattacharya001/agentlens/commit/5f587e496410a022b4a6dcf889aacdb133ac55c0))
* deploy dashboard to GitHub Pages and fix checkout action version ([3c0542c](https://github.com/sauravbhattacharya001/agentlens/commit/3c0542cf177b0948ea772e26173bec62b0a321a9))
* **events:** report truncated fields in ingest response (closes [#152](https://github.com/sauravbhattacharya001/agentlens/issues/152)) ([dfac381](https://github.com/sauravbhattacharya001/agentlens/commit/dfac3816e17caa5f6f58cc2879168763352559bc))
* **forecast:** hoist regression variables to avoid ReferenceError in trend detection ([d0f341a](https://github.com/sauravbhattacharya001/agentlens/commit/d0f341a6ad2443866e75eb455356937e131689d8))
* harden CSV export against formula injection and filename header injection ([5e42710](https://github.com/sauravbhattacharya001/agentlens/commit/5e4271038fb953c8070c18234812985b2f542a3a))
* P95 percentile used wrong formula + publish version validation ([e57f564](https://github.com/sauravbhattacharya001/agentlens/commit/e57f5644039359cc5e72d1821970f5c080831ee4))
* paginate eventsBySession queries to prevent OOM on large sessions ([14ac279](https://github.com/sauravbhattacharya001/agentlens/commit/14ac279d42ea048e5346e5305ab8ae61453c1092)), closes [#29](https://github.com/sauravbhattacharya001/agentlens/issues/29)
* patch 3 npm audit vulnerabilities (path-to-regexp ReDoS, brace-expansion hang, picomatch injection) ([d8575d3](https://github.com/sauravbhattacharya001/agentlens/commit/d8575d3a2af894f881d42d0a043cfae01874a89a))
* population std dev in AnomalyDetector + SSRF protection for webhooks ([343edb7](https://github.com/sauravbhattacharya001/agentlens/commit/343edb7ab3a2b394b0c865bb3edad10463b57b98))
* prevent KeyError on invalid session IDs and join flush thread on close ([2961f30](https://github.com/sauravbhattacharya001/agentlens/commit/2961f30e8d1acd660f0b97f0835051675c5347d1))
* prevent skipped entries during cache eviction ([ed258da](https://github.com/sauravbhattacharya001/agentlens/commit/ed258da313bbf8cbb8ef6f66d51dfa2de64207f1))
* **profiler:** align SQL column names with actual sessions/events schema ([577cf67](https://github.com/sauravbhattacharya001/agentlens/commit/577cf67e9716d1f6ef23057d03c928d12127b21c))
* push full-text search to SQL, apply LIMIT/OFFSET in DB query ([80bd926](https://github.com/sauravbhattacharya001/agentlens/commit/80bd926c661e6e319a6dd834f0b0a3c1f9513395)), closes [#39](https://github.com/sauravbhattacharya001/agentlens/issues/39)
* rate limiter blocked counter incremented per-rule instead of per-check ([6af7c53](https://github.com/sauravbhattacharya001/agentlens/commit/6af7c534bbe9c9c60f395c1aab602cbc19863b39))
* repair two broken test assertions ([8553702](https://github.com/sauravbhattacharya001/agentlens/commit/8553702b40deed429c7e20e9de40632dccc62348))
* replace bidirectional substring pricing match with delimiter-aware longest prefix match ([f961965](https://github.com/sauravbhattacharya001/agentlens/commit/f961965565c613ae5a32cb3892a235b856e6c88d)), closes [#28](https://github.com/sauravbhattacharya001/agentlens/issues/28)
* replace deprecated asyncio.get_event_loop() with asyncio.run() ([#30](https://github.com/sauravbhattacharya001/agentlens/issues/30)) ([563b564](https://github.com/sauravbhattacharya001/agentlens/commit/563b5648b9046d60b10289afe812603490c73a41))
* replace N+1 query in getEligibleSessions exempt tag filtering ([bf0e943](https://github.com/sauravbhattacharya001/agentlens/commit/bf0e943e048e20d56d6cfef871353f3a86d4812a))
* **security:** harden severity classification and annotation input validation ([b4c92ab](https://github.com/sauravbhattacharya001/agentlens/commit/b4c92ab8866e6c184bc6dc7526cf74b3671a80f3))
* **security:** HTML-escape user-controlled data in dashboard template (CWE-79) ([4a3d4c0](https://github.com/sauravbhattacharya001/agentlens/commit/4a3d4c005e178150f2c44bba1a915fbca679d4bd))
* **security:** prevent cache poisoning and add Cache-Control headers ([34492e8](https://github.com/sauravbhattacharya001/agentlens/commit/34492e8e5b8eefed04159acdf39bdebadf3255fb))
* **security:** prevent DNS rebinding SSRF in webhook delivery ([6758b51](https://github.com/sauravbhattacharya001/agentlens/commit/6758b51e91db4f6efa0dff6be35a5e572a2f52a4))
* **security:** prevent SSRF bypass via DNS validation error swallowing ([d084183](https://github.com/sauravbhattacharya001/agentlens/commit/d0841834f9b789f11314f706491d31728dffe9cc))
* **security:** URL-encode CLI query parameters to prevent CWE-74 injection ([3f0fe30](https://github.com/sauravbhattacharya001/agentlens/commit/3f0fe30b7ad8c109707200d6b1aa764868a0ef57))
* **security:** use SHA-256 hash for cache key differentiation instead of API key prefix ([ef92322](https://github.com/sauravbhattacharya001/agentlens/commit/ef92322810338eb0ec14c6ca888e3723cefa6b98))
* sessionsOverTime returns most recent 90 days instead of oldest ([#19](https://github.com/sauravbhattacharya001/agentlens/issues/19)) ([b3b5af0](https://github.com/sauravbhattacharya001/agentlens/commit/b3b5af04d2f4deb79de0f51f70313cc4652c7089))
* timing side-channel in API key auth & correlated subquery bugs in error analytics ([bafc0e4](https://github.com/sauravbhattacharya001/agentlens/commit/bafc0e4dde5053f9687495bd85cbb244438b1cc1))
* Transport.close() race condition ([#59](https://github.com/sauravbhattacharya001/agentlens/issues/59)) ([#67](https://github.com/sauravbhattacharya001/agentlens/issues/67)) ([7a956f7](https://github.com/sauravbhattacharya001/agentlens/commit/7a956f7dd9e0e81e612618b89df1f1790317c5bf))
* use correct event for end-time estimation in SessionCorrelator ([7fcf828](https://github.com/sauravbhattacharya001/agentlens/commit/7fcf8289a591ca285815ef48abd61250934dc39e))
* use sample stddev in anomaly detector, document 4 missing API sections ([#79](https://github.com/sauravbhattacharya001/agentlens/issues/79)) ([d3845ed](https://github.com/sauravbhattacharya001/agentlens/commit/d3845eda9b8f1c273096e97219380784d85176a5))
* use sample variance (Bessel's correction) in anomaly detector baseline ([906511e](https://github.com/sauravbhattacharya001/agentlens/commit/906511ea332e99ccb595e1e605f8418de3f740e0))
* use sample variance (Bessel's correction) in AnomalyDetector ([#22](https://github.com/sauravbhattacharya001/agentlens/issues/22)) ([f7dff5e](https://github.com/sauravbhattacharya001/agentlens/commit/f7dff5ed56356ae31220e29f6e8482c041a472b8)), closes [#21](https://github.com/sauravbhattacharya001/agentlens/issues/21)
* validate session status against allowed values in session_end handler ([450ead9](https://github.com/sauravbhattacharya001/agentlens/commit/450ead9e93eef62ed1b794b9e276249bebbf926e))
* **webhooks:** block HTTP redirects to prevent SSRF bypass ([e9cd62b](https://github.com/sauravbhattacharya001/agentlens/commit/e9cd62b5d6f602cffcb91a2b348166adb2b5e81e))
* **webhooks:** block HTTP redirects to prevent SSRF bypass ([34f5d28](https://github.com/sauravbhattacharya001/agentlens/commit/34f5d2883bc22fbd5852b7cb560fc1c41e6bba23))
* wrap 22 route handlers with wrapRoute() to prevent process crashes (fixes [#44](https://github.com/sauravbhattacharya001/agentlens/issues/44)) ([#47](https://github.com/sauravbhattacharya001/agentlens/issues/47)) ([7efa3d0](https://github.com/sauravbhattacharya001/agentlens/commit/7efa3d07b8437a5b5336bb3da763ec9df9d2d588))


### Performance Improvements

* add in-memory response cache for analytics and leaderboard ([090e539](https://github.com/sauravbhattacharya001/agentlens/commit/090e539d5ff4716523cf6374226e5bfc3e9fcc68))
* add init guards to ensureXxxTable functions ([c3ca2be](https://github.com/sauravbhattacharya001/agentlens/commit/c3ca2be4d69cb7c1ade12b8d8b39953c6f103391))
* add init guards to ensureXxxTable functions to prevent redundant DDL per request ([206d27b](https://github.com/sauravbhattacharya001/agentlens/commit/206d27be670f929c76c8158d89da0c6d903b18c2))
* add LRU prepared-statement cache for dynamic SQL queries ([d2885df](https://github.com/sauravbhattacharya001/agentlens/commit/d2885df7709fe90dfb000f7e8da6f3ff5ad9a3d1))
* add periodic eviction of expired cache entries ([3dcb240](https://github.com/sauravbhattacharya001/agentlens/commit/3dcb240675856d0c78c9f70e49942dea980664e6))
* add periodic eviction of expired cache entries ([f507f7b](https://github.com/sauravbhattacharya001/agentlens/commit/f507f7bffdd912bc851c888ff72c26e25dd9810e))
* amortize entropy syscall with pre-allocated random ID pool ([bff0eb5](https://github.com/sauravbhattacharya001/agentlens/commit/bff0eb5a35ab170e72ab4dab0d66ebdc5f551d1b))
* **anomalies:** cache baseline computations for 15s to avoid redundant full-table scans ([d584a23](https://github.com/sauravbhattacharya001/agentlens/commit/d584a23d903e69b2e9630514446620f82233b9be))
* **anomalies:** single-pass baseline computation with sum-of-squares ([92e4361](https://github.com/sauravbhattacharya001/agentlens/commit/92e4361ceca1c51b0f32e48f618cf9e6caa45010))
* avoid redundant O(n) sum in latencyStats and cache API key hashes in middleware ([44ae054](https://github.com/sauravbhattacharya001/agentlens/commit/44ae0548e3bc225ab62d485502a13158848228c4))
* batch retention purge into single transaction and eliminate N+1 queries ([4bd3f25](https://github.com/sauravbhattacharya001/agentlens/commit/4bd3f25ab3ae5692cfab2bad45017b60893ba72a))
* batch session upserts and token updates in event ingestion ([dc0b817](https://github.com/sauravbhattacharya001/agentlens/commit/dc0b8178fa23ae0e9d21e5597463cf1ed8671381))
* cache baseline computations in AnomalyDetector ([a572a2e](https://github.com/sauravbhattacharya001/agentlens/commit/a572a2e252f58d496e1392eb010fa5c76bf13435))
* cache baseline computations in AnomalyDetector ([40af0e0](https://github.com/sauravbhattacharya001/agentlens/commit/40af0e0bfa7f1e2a761f0d4ac22782496c9f8903))
* cache daily aggregates in CostForecaster, sweep-line overlap detection in SessionCorrelator ([64a2992](https://github.com/sauravbhattacharya001/agentlens/commit/64a2992fa4a2754fb704b5a6806bb721b3aa8eb4))
* cache extractServiceName results and optimize isFailure regex ([78acc42](https://github.com/sauravbhattacharya001/agentlens/commit/78acc42a5b15670dc0555e4f2cc58785b917b7d4))
* cache fingerprint_id on ErrorOccurrence to avoid recomputation ([9cc720d](https://github.com/sauravbhattacharya001/agentlens/commit/9cc720d44833cf94c9ba8e0b45dae5507750f402))
* cache fingerprint_id on ErrorOccurrence to avoid recomputation in _compute_trends ([2e8a41f](https://github.com/sauravbhattacharya001/agentlens/commit/2e8a41fcd42d730960e044d2f75320dc28e7e1ac))
* cache loadPricingMap() with 60s TTL to eliminate redundant DB queries ([7b4b0f4](https://github.com/sauravbhattacharya001/agentlens/commit/7b4b0f4673f03ca139709999367c2b44bff656c4))
* cache prepared statements for /analytics/performance endpoint ([5bf98e2](https://github.com/sauravbhattacharya001/agentlens/commit/5bf98e25fccf20f45e2347397b127395c6a90e98))
* cache prepared statements for heatmap and costs endpoints ([1089c93](https://github.com/sauravbhattacharya001/agentlens/commit/1089c93850c41c2057a549761a68fab85d486729))
* cache prepared statements in command-center routes ([0d9e398](https://github.com/sauravbhattacharya001/agentlens/commit/0d9e398af0dcec1b20fc0a0480854959d5dfdb54))
* cache prepared statements in correlations module ([3ea1ac8](https://github.com/sauravbhattacharya001/agentlens/commit/3ea1ac872d90149891d64f0bbc7bbd35070f7919))
* cache prepared statements in forecast routes ([f2e16d8](https://github.com/sauravbhattacharya001/agentlens/commit/f2e16d87852036033aa2796579c400a9b7215067))
* **capacity:** single-pass peak_utilization + reuse _compute_all_trends in detect_bottlenecks ([004d6f5](https://github.com/sauravbhattacharya001/agentlens/commit/004d6f5d0e4b2973430536b32a669c9b47bfaaac))
* compute MTBF in SQL and wrap error queries in transaction ([e2300ac](https://github.com/sauravbhattacharya001/agentlens/commit/e2300ac82f857df34dbe6e33f1b178b74a59f355))
* compute retention age distribution in SQL instead of JS ([c8a1e1e](https://github.com/sauravbhattacharya001/agentlens/commit/c8a1e1e30f8f15da3040ab7a405f664245956c6d)), closes [#20](https://github.com/sauravbhattacharya001/agentlens/issues/20)
* consolidate summary stats to single-pass loop in event search and compare endpoints ([8baef6c](https://github.com/sauravbhattacharya001/agentlens/commit/8baef6c623a53911b1d54d7690b70220a284f826))
* **correlation:** build shared event index to eliminate 3 redundant full-event scans ([c512118](https://github.com/sauravbhattacharya001/agentlens/commit/c512118a32f341acf96b8bc6894bc1a2cb957978))
* **drift:** single-pass event metric extraction in _extract_session_metrics ([e1ae10c](https://github.com/sauravbhattacharya001/agentlens/commit/e1ae10c1a441f591f5469b2b762bf44778f3f3ff))
* eliminate N+1 query in GET /correlations/groups ([df4efa9](https://github.com/sauravbhattacharya001/agentlens/commit/df4efa90e86182baec26c27403972ebd21404ee7))
* eliminate N+1 query in GET /correlations/groups ([4b33a24](https://github.com/sauravbhattacharya001/agentlens/commit/4b33a24376ffb01a6365c762cdffcbc3a36738f7))
* eliminate N+1 tag queries in /sessions/by-tag/:tag ([c263f87](https://github.com/sauravbhattacharya001/agentlens/commit/c263f87f899bd2507949c91c3275938a4fc4da64))
* eliminate redundant full-table scan in /analytics/performance ([#46](https://github.com/sauravbhattacharya001/agentlens/issues/46)) ([b81c20a](https://github.com/sauravbhattacharya001/agentlens/commit/b81c20a39c2aba1d65f8fb56d12207458622bda9))
* eliminate redundant recomputation in CapacityPlanner.report() ([41bc262](https://github.com/sauravbhattacharya001/agentlens/commit/41bc262279603041128a4a71924490b61edb6724))
* eliminate spread-copy in parseEventRow and reduce allocations in export pipeline ([bb2d56a](https://github.com/sauravbhattacharya001/agentlens/commit/bb2d56a918856bcd13f7aff6e1db33694c1e9330))
* **error_fingerprint:** cache normalisation, hashing, and trend computation ([018974b](https://github.com/sauravbhattacharya001/agentlens/commit/018974b4ea8bf695206e74aa3446e9709db520f2))
* fix LRU cache pollution in session search and O(n) lookup in anomaly detection ([59b3365](https://github.com/sauravbhattacharya001/agentlens/commit/59b336591d4b39f6c3a616d7d11b002ae7cdb66c))
* fix Map deletion during iteration + cache JSON parsing ([d20520d](https://github.com/sauravbhattacharya001/agentlens/commit/d20520d3dc65607ff6349e5eda4bf6297743a485))
* fix Map deletion during iteration + cache JSON parsing in correlateByMetadata ([e18373f](https://github.com/sauravbhattacharya001/agentlens/commit/e18373f3b86a645e7b72c20866d60c41afa2ff8f))
* **flamegraph:** optimize tree traversal and event placement ([72531c7](https://github.com/sauravbhattacharya001/agentlens/commit/72531c7790020ec8f7c8af37058d5bd2de74c603))
* **forecast:** cache model aggregates and eliminate redundant sorts in spending_summary/check_budget ([6bfb882](https://github.com/sauravbhattacharya001/agentlens/commit/6bfb88220737c53602452a0d0d0da76546eba517))
* **forecast:** optimize linearRegression to single-pass and reduce redundant array coercions ([458fc82](https://github.com/sauravbhattacharya001/agentlens/commit/458fc82751f847bb3d9b6eff743f81ed516d3d3c))
* **latency:** cache step_baselines() to avoid redundant recomputation ([3f846ee](https://github.com/sauravbhattacharya001/agentlens/commit/3f846ee12948b5712d04475a05937b924d9554a5))
* **latency:** single-pass step_counts() eliminates 4x iteration in fleet_summary ([f5e4707](https://github.com/sauravbhattacharya001/agentlens/commit/f5e470788a1e005020eb333028915c4c1d9c5d9d))
* **leaderboard:** consolidate 3 DB queries into single CTE query ([fab9e56](https://github.com/sauravbhattacharya001/agentlens/commit/fab9e56607512a97b05e951a5e8b4827dc1029da))
* maintain running total in _SlidingWindow for O(1) total() ([66a455d](https://github.com/sauravbhattacharya001/agentlens/commit/66a455dcd9d276caf8b043e9dee569f56f262efb))
* memoize findPricing() to eliminate repeated O(k) prefix scans ([26ef571](https://github.com/sauravbhattacharya001/agentlens/commit/26ef57106b92cffa4d435abed680a77c41d41ea6))
* merge two per-group duration queries into one in /performance ([b12dc93](https://github.com/sauravbhattacharya001/agentlens/commit/b12dc939ee6dbdc54b37406475e419ed89dee53d))
* **narrative:** single-pass event classification and model aggregation ([1d14fa3](https://github.com/sauravbhattacharya001/agentlens/commit/1d14fa3c0e5b8b896294ff620d468ba48c7fd4b6))
* O(1) sliding window total via running counter ([7ae30bd](https://github.com/sauravbhattacharya001/agentlens/commit/7ae30bd4469fc0d1e0c9b00b0c8176d964529e9a))
* O(E·D·logS) flamegraph event-to-span placement + cached sentence tokenization in coherence scoring ([8c55b7e](https://github.com/sauravbhattacharya001/agentlens/commit/8c55b7e14cc6490dedd6d111225e0ed6a22d0e46))
* O(n log n) Mann-Whitney U via sort-based rank-sum + eliminate redundant stat recomputation in analyze() ([4ac9479](https://github.com/sauravbhattacharya001/agentlens/commit/4ac947977856e9eb3e4f9485d46af7aa3d4c3744))
* optimize AgentEvent.to_api_dict() with manual fast path ([367daf3](https://github.com/sauravbhattacharya001/agentlens/commit/367daf3084cf5523906a9094abf2d02dfc3c9566))
* optimize error propagation (bisect) and contention detection (eliminate re-scan) ([575e006](https://github.com/sauravbhattacharya001/agentlens/commit/575e00654da7c1e0faaeccefaeda00c4eb01305c))
* optimize event ID generation, cache eviction, and tag batch queries ([bc7d617](https://github.com/sauravbhattacharya001/agentlens/commit/bc7d6176925fddd31b30f952f9fc62e053294f5c))
* optimize purgeExpired to break early using Map insertion order ([67ee5d2](https://github.com/sauravbhattacharya001/agentlens/commit/67ee5d2f16ade12972279e005efb2d672c984da4))
* optimize replay frame endpoint to avoid loading all events ([a617e66](https://github.com/sauravbhattacharya001/agentlens/commit/a617e66d212a7267a2b629c52e0e4f9fe9392e8e))
* optimize transport buffer operations ([ac1e8c6](https://github.com/sauravbhattacharya001/agentlens/commit/ac1e8c6623ccac792b4a977d67adc380259684f2))
* pass precomputed sums to latencyStats, sort durations in-place, pre-allocate CSV array ([0677270](https://github.com/sauravbhattacharya001/agentlens/commit/06772702e7dddc101ff13a8e3f39ffa860f8339c))
* **PostmortemGenerator:** single-pass _assess_impact over errors — merged 5 iterations (tool/model set-building + 3 sum() + 1 any()) into one loop with frozenset lookup for user_facing types, reducing O(5E) to O(E) ([45eb56d](https://github.com/sauravbhattacharya001/agentlens/commit/45eb56dd9f34e4321ddf9125e3f8280d19b8eaf8))
* **postmortem:** pre-parse timestamps once and use bisect for O(log E) phase classification ([a9b8f35](https://github.com/sauravbhattacharya001/agentlens/commit/a9b8f352081f8a8b4055ccf81dc41f676deec346))
* pre-compute API key hash at init time instead of per-request ([7f97ac2](https://github.com/sauravbhattacharya001/agentlens/commit/7f97ac2d34dc4457213a67ccfe35043789436c17))
* pre-compute LCS keys + fast-path identical sequences in session_diff ([5358773](https://github.com/sauravbhattacharya001/agentlens/commit/5358773f2e4d5cbbc0b0f7165724eb9cb7440468))
* pre-compute timestamps and index-based causal chain correlation ([571e714](https://github.com/sauravbhattacharya001/agentlens/commit/571e7144460561cc14b3d7569474a85bb90cb881))
* push analytics/performance aggregation to SQL ([1aca9a2](https://github.com/sauravbhattacharya001/agentlens/commit/1aca9a2dc020a0541ecaaa95c46170723e86828a))
* push event search filters to SQL + doc: SDK analysis modules ([9811969](https://github.com/sauravbhattacharya001/agentlens/commit/9811969b1edbdf1d88c2640e13295516533156df))
* **quota:** add per-entity record index for O(entity) lookups ([38e18db](https://github.com/sauravbhattacharya001/agentlens/commit/38e18db18a45267f11a37556831482ef649502c0))
* reduce diff LCS memory from O(n*m) to O(m) + compact direction bits ([299b5ee](https://github.com/sauravbhattacharya001/agentlens/commit/299b5ee060626d433aa9b054a31227709a63d97f))
* replace correlated subqueries with JOIN aggregation in errorSessions ([2bf80e7](https://github.com/sauravbhattacharya001/agentlens/commit/2bf80e7fe002e9b929241065f7ff509a6bc5df76)), closes [#34](https://github.com/sauravbhattacharya001/agentlens/issues/34)
* replace O(n*m) Array.includes with Set lookups in profiler list endpoint ([f503d53](https://github.com/sauravbhattacharya001/agentlens/commit/f503d5320e599ff576857e17f06d7098f5535bc2))
* replace O(n²) contention detection with sweep-line algorithm ([084d778](https://github.com/sauravbhattacharya001/agentlens/commit/084d778c4d172363dfd4f5286b96eba11955b777))
* replace uuid package with native crypto.randomUUID() ([b8b0f7b](https://github.com/sauravbhattacharya001/agentlens/commit/b8b0f7b95423175597d31613eb57ea7d5cb1f88d))
* replace uuid package with native crypto.randomUUID() ([7f4274e](https://github.com/sauravbhattacharya001/agentlens/commit/7f4274e3f33d0472abcac23cb43cb8eb553723d7))
* **replay:** skip JSON parsing in /summary endpoint ([447a0b5](https://github.com/sauravbhattacharya001/agentlens/commit/447a0b5b630fb53189cece3baa5c1c84f6564952))
* reuse event index in _max_concurrent_usage, eliminating O(R*E) scan ([d48b5d9](https://github.com/sauravbhattacharya001/agentlens/commit/d48b5d9172d510ff3a7477cd7c50b94b386a8f5a))
* reuse shared event index in find_sync_points() instead of re-scanning all events ([9c72e77](https://github.com/sauravbhattacharya001/agentlens/commit/9c72e77d638da0f6c6e8428deafc1b9155b6ff92))
* serve static assets before API middleware stack + add covering index for analytics ([44673e7](https://github.com/sauravbhattacharya001/agentlens/commit/44673e7c15a3203017d3077463b25b990817f47e))
* single-pass _compute_stats + inverted tag index in PromptVersionTracker ([8b41146](https://github.com/sauravbhattacharya001/agentlens/commit/8b41146be104ed451e554c7f06b9b80bf3c66756))
* single-pass _compute_trend with closed-form x_mean in CapacityPlanner ([ed58d70](https://github.com/sauravbhattacharya001/agentlens/commit/ed58d706ddd222671e0a29c221d399884128a92a))
* single-pass event aggregation in computeSessionMetrics ([c76b6b7](https://github.com/sauravbhattacharya001/agentlens/commit/c76b6b71a3bd5ed0db0a423617bf7fb259315bbc))
* single-pass event aggregation in HealthScorer.score() ([32d3458](https://github.com/sauravbhattacharya001/agentlens/commit/32d3458265109b73d23a4a374584297cbc939014))
* single-pass event iteration in extract_metrics ([5d6c369](https://github.com/sauravbhattacharya001/agentlens/commit/5d6c369c35708c4ef33b2bef4d247791a07c62c9))
* single-pass event iteration in extract_metrics ([5726a20](https://github.com/sauravbhattacharya001/agentlens/commit/5726a20d4e2213f9d11fab6d0ed0517c80718520))
* single-pass GroupStats initialization (7 passes → 1) ([3757803](https://github.com/sauravbhattacharya001/agentlens/commit/375780378593c0697e5923d9fdcb664ef969f890))
* single-pass metric extraction in CapacityPlanner trend/projection ([4e4d8b9](https://github.com/sauravbhattacharya001/agentlens/commit/4e4d8b9baaff8af1c53cc243dbf844b5caca1eb2))
* **sla:** single-pass metric collection in SLAEvaluator ([6d7b83c](https://github.com/sauravbhattacharya001/agentlens/commit/6d7b83c3e4ca9627953e11541f2138417d14c6d0))
* stream CSV export and skip redundant event count ([ac0d507](https://github.com/sauravbhattacharya001/agentlens/commit/ac0d5074f55e398100e6954eb7daaded37f0cbdc))
* stream CSV export and skip redundant event count query ([7cceb17](https://github.com/sauravbhattacharya001/agentlens/commit/7cceb17f33208c6bc5b2ec2b87112771c3dba62b))
* sweep-line contention detection (O(n²) → O(n log n)) ([4003cd6](https://github.com/sauravbhattacharya001/agentlens/commit/4003cd6fb6b0533387b6c9d140ab98b16f57105a))
* use bisect for _recent_samples in CapacityPlanner ([0ef39f0](https://github.com/sauravbhattacharya001/agentlens/commit/0ef39f061c948ede7a08b320c02beaee9cb59402))

## [1.2.0] - 2026-03-06

### Added

- **Incident Postmortem Generator** — Generate post-incident reports from session data (SDK + backend)
- **Trace Correlation Rules Engine** — Define rules for auto-correlating related traces with scheduled auto-correlation, SSE streaming, and deduplication
- **Response Quality Evaluator** — Score agent output quality across multiple dimensions
- **Service Dependency Map** — Visualize tool/API usage patterns and service relationships
- **Trace Sampling & Rate Limiting** — Production-ready sampling policies and rate control
- **Activity Heatmap** — Day-of-week × hour-of-day interaction matrix visualization
- **SLA Monitor** — Service-level compliance tracking and alerting
- **Behavioral Drift Detection** — Detect changes in agent behavior patterns over time
- **Compliance Checker** — Policy-based session validation and audit
- **Cost Forecaster** — Predict future AI costs from historical usage trends
- **Session Search** — Full-text search, filter, and sort across sessions

### Fixed

- **BudgetTracker session collision** — Multiple budgets per session no longer overwrite each other (#35)
- **CSV formula injection** — Harden CSV export against spreadsheet injection attacks
- **OOM on large sessions** — Paginate eventsBySession queries to prevent memory exhaustion
- **Pricing model match** — Replace bidirectional substring match with delimiter-aware longest prefix
- **N+1 tag filtering** — Eliminate per-session tag queries in retention exempt filtering
- **P95 formula** — Correct percentile calculation in analytics
- **AnomalyDetector variance** — Use Bessel's correction (sample variance) for small datasets (#22)
- **AlertManager cooldown** — Fix race condition in alert evaluate cooldown tracking
- **sessionsOverTime** — Return most recent 90 days instead of oldest (#19)
- **Deprecated asyncio** — Replace `get_event_loop()` with `asyncio.run()` (#30)

### Performance

- Replace correlated subqueries with JOIN aggregation in error analytics
- Batch retention purge into single transaction, eliminate N+1 queries
- Push analytics/performance aggregation and event search filters to SQL
- Compute retention age distribution in SQL instead of JS
- Cache prepared statements and add database indexes
- Eliminate N+1 tag queries in `/sessions/by-tag/:tag`

### Security

- Constant-time comparison for API key authentication (prevent timing side-channel)
- Mask API key in `repr` output, validate webhook ID parameters
- Input bounds validation for webhook configuration
- SSRF protection for outbound webhooks
- Replace `Math.random` IDs with `crypto.randomBytes`

### Refactored

- Extract shared pagination, session-ID validation, and error-handling helpers
- Extract session tag routes into dedicated `tags.js` module
- Extract Transport HTTP helpers and `_resolve_session` in tracker
- Extract statistical utilities into shared stats module
- Extract session metrics computation into shared module
- Adopt request-helpers across all route files

### Tests

- 32 new Transport convenience HTTP method tests
- 58 sessions test suite
- `node:test` compatible unit tests for `db.js` schema init
- Converted db and webhook tests from `node:test` to Jest

### Documentation

- SDK documentation for 8 previously undocumented modules
- Sampling & rate limiting documentation page
- JSDoc added to all 15 route handlers in `sessions.js`
- SDK analysis modules documentation

## [1.1.0] - 2026-02-19

### Added

- **Cost Estimation** — Full cost tracking across sessions and events
  - `model_pricing` DB table with default pricing for 14 popular models (GPT-4/4o/3.5, Claude 3/3.5/4, Gemini Pro/Flash)
  - `GET /pricing` — List all model pricing configuration
  - `PUT /pricing` — Update pricing for one or more models
  - `DELETE /pricing/:model` — Remove custom pricing
  - `GET /pricing/costs/:sessionId` — Calculate per-event and per-model costs with fuzzy model matching
  - Dashboard **💲 Costs tab** with cost overview cards, per-event cost bar chart, cumulative cost line chart, cost-by-model table, top costliest events list, and inline pricing editor
  - SDK methods: `get_costs()`, `get_pricing()`, `set_pricing()` with full module-level API
  - 12 new SDK tests (82 total)

## [1.0.0] - 2026-02-14

### 🎉 Initial Stable Release

AgentLens v1.0.0 — Observability and explainability for AI agents. Track agent sessions, tool calls, LLM interactions, and costs in real-time with a lightweight Python SDK and Node.js dashboard.

### Added

- **Python SDK** (`agentlens` package)
  - `@track_agent` and `@track_tool_call` decorators with full async support
  - Pydantic-based data models (`AgentEvent`, `ToolCallEvent`, `LLMEvent`, `Session`)
  - Batched HTTP transport with automatic retry and backpressure handling
  - Configurable `AgentTracker` with API key authentication and custom endpoints
  - LangChain integration support

- **Backend API** (Node.js + Express)
  - RESTful endpoints for session and event ingestion
  - SQLite-backed persistence via `better-sqlite3`
  - CORS-enabled for cross-origin dashboard access
  - Seed script for demo data generation

- **Dashboard** (Vanilla JS SPA)
  - Real-time session list with status indicators
  - Event timeline visualization per session
  - Tool call and LLM interaction detail views

- **Documentation Site** (12 pages)
  - Getting started guide and quickstart tutorial
  - Full SDK reference and API documentation
  - Architecture overview and deployment guide
  - Decorator reference, transport internals, and database schema docs

- **DevOps & Tooling**
  - CodeQL security scanning (JavaScript + Python)
  - Dependabot configuration (pip, npm, GitHub Actions)
  - Issue and PR templates
  - GitHub Copilot coding agent setup (setup-steps + instructions)

### Fixed

- Unbounded buffer growth and event loss in SDK transport layer
- Batch-length retry key replaced with consecutive failure counter
- Duplicate license section in README

### Changed

- Rebranded from AgentOps to AgentLens

[1.2.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.2.0
[1.1.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.1.0
[1.0.0]: https://github.com/sauravbhattacharya001/agentlens/releases/tag/v1.0.0
