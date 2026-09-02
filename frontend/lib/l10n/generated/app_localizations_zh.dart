// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Chinese (`zh`).
class AppLocalizationsZh extends AppLocalizations {
  AppLocalizationsZh([String locale = 'zh']) : super(locale);

  @override
  String get appTitle => 'AI 修圖';

  @override
  String get appCompactTitle => 'AI 修圖';

  @override
  String get languageTraditionalChinese => '繁體中文';

  @override
  String get languageEnglish => '英文';

  @override
  String get switchToTraditionalChinese => '將介面切換為繁體中文';

  @override
  String get switchToEnglish => '將介面切換為英文';

  @override
  String get themeLight => '淺色';

  @override
  String get themeDark => '深色';

  @override
  String get switchToLightTheme => '切換為淺色模式';

  @override
  String get switchToDarkTheme => '切換為深色模式';

  @override
  String get clearCurrentWork => '清除目前工作';

  @override
  String get chooseOriginal => '選擇原圖';

  @override
  String get changeOriginal => '更換原圖';

  @override
  String get toolPrompt => '指令';

  @override
  String get toolAutoModels => '模型';

  @override
  String get toolStyles => '風格';

  @override
  String get toolReference => '參考';

  @override
  String get toolManual => '調整';

  @override
  String get toolHistory => '歷史';

  @override
  String get autoModelTitle => '自動修圖比較';

  @override
  String get autoModelSubtitle => '從目前版本一次建立並保存兩個候選';

  @override
  String get autoModelSource => '共同來源';

  @override
  String get autoModelExpertTitle => '專家忠實';

  @override
  String get autoModelExpertDescription => '較克制、接近所選 Expert C 專家成品的訓練結果。';

  @override
  String get autoModelVividTitle => '鮮明對比';

  @override
  String get autoModelVividDescription => '對比與色彩更強、較明亮鮮豔的另一種詮釋。';

  @override
  String get autoModelRun => '產生兩個候選';

  @override
  String get autoModelRetry => '重試或取回候選';

  @override
  String get autoModelRunning => '正在產生兩個候選…';

  @override
  String get autoModelCancel => '停止等待';

  @override
  String get autoModelSelect => '使用這個版本';

  @override
  String get autoModelSelected => '目前選取';

  @override
  String get autoModelSourceHint => '目前版本會成為兩個候選的共同 parent；成功結果都會永久保留在歷史。';

  @override
  String get autoModelRepeatHint => '這個來源已包含自動修圖；仍可再次執行，但效果可能會加強。';

  @override
  String get autoModelNoSource => '請先選擇原圖或一個已保存版本。';

  @override
  String get autoModelCandidateFailed => '這個候選未完成';

  @override
  String get autoModelCancelledHint =>
      '後端可能仍會完成；再次產生會連回同一請求，既有工作階段也可從歷史取回已保存結果。';

  @override
  String get labelOriginal => '原圖';

  @override
  String get labelCompare => '對比';

  @override
  String get labelResult => '結果';

  @override
  String get labelPreview => '預覽';

  @override
  String get labelBefore => '之前';

  @override
  String get labelAfter => '之後';

  @override
  String get comparisonBaseline => '比較基準';

  @override
  String get comparisonBaselineOriginal => '原圖';

  @override
  String get comparisonBaselineParent => '上一步';

  @override
  String get comparisonParentUnavailable => '這個版本沒有可用的上一步，已改用原圖比較。';

  @override
  String get comparisonDragHandle => '前後對比分割線';

  @override
  String comparisonDragHandleValue(int percent) {
    return '前後對比分割位置 $percent%';
  }

  @override
  String get comparisonMoveLeft => '顯示更多結果';

  @override
  String get comparisonMoveRight => '顯示更多比較基準';

  @override
  String get resetZoom => '重設視角';

  @override
  String get holdToSeeOriginal => '按住照片可暫時查看原圖';

  @override
  String get dismissHint => '關閉提示';

  @override
  String get selectPhotoToStart => '選擇一張照片開始';

  @override
  String get photoWorkspaceDescription => '照片會完整顯示，修圖結果與歷史版本都會保留。';

  @override
  String get photoWorkspaceCompactDescription => '照片、結果與歷史版本都會保留。';

  @override
  String get selectOriginal => '選擇原圖';

  @override
  String get resultAppearsHere => '完成修圖後，結果會顯示在這裡';

  @override
  String get noImage => '尚無圖片';

  @override
  String get processing => '處理中…';

  @override
  String get imageLoadFailed => '圖片載入失敗';

  @override
  String get discardDraftTitle => '捨棄尚未套用的調整？';

  @override
  String get discardDraftForHistory => '切換歷史版本會捨棄目前手動調整草稿。';

  @override
  String get discardDraftForOriginal => '回到原圖建立新分支會捨棄目前手動調整草稿。';

  @override
  String get discardPhotoGitForTool => '開啟其他工具會捨棄目前的版本操作與預覽。';

  @override
  String get actionBack => '返回';

  @override
  String get actionDiscardAndSwitch => '捨棄並切換';

  @override
  String get replaceOriginalTitle => '更換原始圖片？';

  @override
  String get replaceOriginalMessage => '更換後會清除目前 session、未套用的手動草稿與未完成的版本操作。';

  @override
  String get actionCancel => '取消';

  @override
  String get actionReplaceImage => '更換圖片';

  @override
  String imagePickFailed(String error) {
    return '無法選擇圖片：$error';
  }

  @override
  String get clearWorkTitle => '清除目前工作？';

  @override
  String get clearWorkMessage => '畫面會回到初始狀態並捨棄未完成的草稿，後端已保存的歷史不會被刪除。';

  @override
  String get actionClearScreen => '清除畫面';

  @override
  String get promptEditTitle => '指令修圖';

  @override
  String get promptBranchFromOriginal => '從原圖建立新的歷史分支';

  @override
  String get promptFirstVersionFromOriginal => '從原圖建立第一個版本';

  @override
  String get promptContinueSelected => '從目前選中的版本繼續調整';

  @override
  String get promptHint => '例如：亮度加十、套用百分百經典電影感，或合併版本四和版本六';

  @override
  String get promptModeNotice => '一次輸入或口說一個動作；套用後會自動分流到修圖、精確調參、風格或版本工具。';

  @override
  String get commandPlanning => '正在理解指令…';

  @override
  String get commandPlanTitle => '指令執行計畫';

  @override
  String get commandPreviewNotice => '版本操作一定先產生預覽，只有你確認後才會建立新版本。';

  @override
  String get speechLanguageLabel => '辨識語言';

  @override
  String get speechLanguageHelp => '短指令請明確選中文或英文；中英混用再使用自動偵測。';

  @override
  String get speechLanguageTraditionalChinese => '繁體中文';

  @override
  String get speechLanguageEnglish => 'English';

  @override
  String get speechLanguageAutomatic => '自動偵測';

  @override
  String speechResultMetadata(String language, String model) {
    return '辨識為 $language · $model';
  }

  @override
  String get speechStart => '使用麥克風';

  @override
  String get speechStop => '停止';

  @override
  String get speechCancel => '取消';

  @override
  String get speechRequestingPermission => '正在請求麥克風權限…';

  @override
  String speechRecordingSeconds(int seconds) {
    return '錄音中 · $seconds 秒';
  }

  @override
  String get speechTranscribing => '正在將語音轉成可編輯文字…';

  @override
  String get speechPrivacyNotice => '音訊只交給本機後端處理，不會寫入修圖歷史。';

  @override
  String get speechUnavailable => '目前無法使用麥克風輸入，仍可直接輸入文字指令。';

  @override
  String get statusSpeechCompleted => '語音已加入可編輯文字，確認內容後再套用指令。';

  @override
  String get statusSpeechCancelled => '已取消這次語音輸入。';

  @override
  String get errorSpeechPermissionDenied => '麥克風權限被拒絕，請在 Chrome 設定中允許，或改用文字輸入。';

  @override
  String get errorSpeechNoMicrophone => '找不到可用的麥克風，請檢查裝置，或改用文字輸入。';

  @override
  String get errorSpeechRecorderUnavailable =>
      '瀏覽器無法提供需要的錄音格式，請使用目前版本的 Chrome，或改用文字輸入。';

  @override
  String get errorSpeechRecordingFailed => '錄音失敗，請檢查麥克風後重試。';

  @override
  String get errorSpeechNoAudio => '麥克風沒有回傳可用音訊，請重新錄音。';

  @override
  String get errorSpeechInvalidAudio => '無法讀取這段錄音，請重新錄音。';

  @override
  String get errorSpeechUnsupportedFormat => '目前不支援這段錄音格式，請在 Chrome 重新錄音。';

  @override
  String get errorSpeechNoSpeech => '沒有偵測到有效語音，請靠近麥克風後重試。';

  @override
  String get errorSpeechTooLong => '錄音超過 15 秒，請改說較短的修圖指令。';

  @override
  String get errorSpeechTooLarge => '錄音檔太大，請改說較短的修圖指令。';

  @override
  String get errorSpeechModelUnavailable => '本機語音模型目前無法使用，請檢查後端模型與裝置設定。';

  @override
  String get errorSpeechTranscriptionFailed => '語音辨識失敗，請重新錄音。';

  @override
  String get errorSpeechTimeout => '語音辨識等待過久，請重新嘗試。';

  @override
  String get errorSpeechBackendUnavailable => '無法連線到本機語音後端，仍可使用文字輸入。';

  @override
  String get applyPrompt => '套用指令';

  @override
  String get styleCatalogTitle => '風格目錄';

  @override
  String get styleCatalogUnavailable => '風格目錄目前無法載入，請確認後端已啟動。';

  @override
  String styleCatalogSubtitle(int count, String version) {
    return '$count 種已核准風格 · v$version';
  }

  @override
  String get styleCategoryPrevious => '向左查看更多分類';

  @override
  String get styleCategoryNext => '向右查看更多分類';

  @override
  String get styleCategoryAll => '全部';

  @override
  String get styleStrength => '強度';

  @override
  String get applyStyle => '套用風格';

  @override
  String get referenceEditTitle => '參考圖修圖';

  @override
  String get referenceFromOriginal => '原圖會依參考圖的色彩方向調整';

  @override
  String get referenceFromCurrent => '從目前版本套用參考圖方向';

  @override
  String get selectReference => '選擇參考圖';

  @override
  String get changeReference => '更換參考圖';

  @override
  String get removeReference => '移除參考圖';

  @override
  String get applyReference => '套用參考圖';

  @override
  String get manualEditTitle => '手動調整';

  @override
  String manualSourceVersion(String target, String mode) {
    return '來源版本 · $target · $mode';
  }

  @override
  String get advancedAdjustments => '進階調整';

  @override
  String get historyTitle => '歷史紀錄';

  @override
  String historyVersionCount(int count) {
    return '版本 $count';
  }

  @override
  String get refreshHistory => '重新同步';

  @override
  String get selectedOriginalNewBranch => '已選原圖 · 下一次建立新分支';

  @override
  String get createBranchFromOriginal => '從原圖建立新分支';

  @override
  String get emptyHistory => '完成第一次修圖後，版本會依序顯示在這裡。';

  @override
  String get currentPreview => '目前預覽';

  @override
  String get currentAdjustments => '目前調整';

  @override
  String styleEffectiveParameters(int strength) {
    return '以下為依 $strength% 強度折算的等效參數；風格實際效果也包含曲線、分色與其他內部配方。';
  }

  @override
  String get noManualParameters => '這個版本沒有可顯示的手動參數。';

  @override
  String styleUnderstanding(String name, int strength) {
    return '理解結果：已套用 $name，強度 $strength%。';
  }

  @override
  String adjustmentCount(int count) {
    return '$count 項微調';
  }

  @override
  String get adaptiveIntervalReset => '已重設區間';

  @override
  String get adaptiveConverged => '已收斂';

  @override
  String get adaptiveContinue => '持續微調';

  @override
  String get adaptiveFineTune => '自適應微調';

  @override
  String get relativeAdjustment => '本次相對調整';

  @override
  String get candidateValue => '候選值';

  @override
  String get currentBounds => '目前界線';

  @override
  String get stepSize => '步幅';

  @override
  String stepSizeWithTransform(String transform) {
    return '步幅（$transform）';
  }

  @override
  String get adaptiveReasonInitial => '建立初始步幅';

  @override
  String get adaptiveReasonReverse => '從目前效果往回收斂';

  @override
  String get adaptiveReasonHandoff => '接手相關參數調整';

  @override
  String get adaptiveReasonMidpoint => '依回饋取區間中點';

  @override
  String get adaptiveReasonContinue => '延續同方向探索';

  @override
  String get adaptiveReasonNarrow => '依反向回饋縮小';

  @override
  String get adaptiveReasonReanchor => '重新建立調整基準';

  @override
  String get adaptiveReasonAbsolute => '採用明確數值並重設區間';

  @override
  String get adaptiveReasonRelative => '依相對數值調整';

  @override
  String get adaptiveReasonResetAxis => '重設單一參數';

  @override
  String get adaptiveReasonResetOriginal => '回到原圖';

  @override
  String get collapse => '收合';

  @override
  String resetParameter(String label) {
    return '將$label設為中性值';
  }

  @override
  String equivalentParameters(String summary) {
    return '等效 $summary';
  }

  @override
  String historyVersionMode(int version, String mode) {
    return '版本 $version · $mode';
  }

  @override
  String get rootBranch => '根分支';

  @override
  String get continuesParent => '接續父版本';

  @override
  String continuesVersion(int version) {
    return '接續版本 $version';
  }

  @override
  String get referenceNotSelected => '尚未選擇參考圖';

  @override
  String get actionReset => '重設';

  @override
  String get actionApply => '套用';

  @override
  String get actionApplying => '套用中…';

  @override
  String get notApplied => '尚未套用';

  @override
  String get parameterExposure => '曝光';

  @override
  String get parameterBrightness => '亮度';

  @override
  String get parameterContrast => '對比';

  @override
  String get parameterHighlights => '高光';

  @override
  String get parameterShadows => '陰影';

  @override
  String get parameterWhites => '白位';

  @override
  String get parameterBlacks => '黑位';

  @override
  String get parameterSaturation => '飽和度';

  @override
  String get parameterVibrance => '自然飽和度';

  @override
  String get parameterTemperature => '色溫';

  @override
  String get parameterWhiteBalanceTint => '白平衡色偏';

  @override
  String get parameterSharpen => '銳化';

  @override
  String get parameterClarity => '清晰度';

  @override
  String get parameterDehaze => '去霧';

  @override
  String get parameterVignette => '暗角';

  @override
  String get regionAll => '全圖';

  @override
  String get regionSky => '天空';

  @override
  String get regionPerson => '人物';

  @override
  String get regionBackground => '背景';

  @override
  String get regionHighlights => '亮部';

  @override
  String get regionShadows => '暗部';

  @override
  String get regionCenter => '中央';

  @override
  String get regionEdges => '邊緣';

  @override
  String get modePrompt => '指令';

  @override
  String get modeAutoModel => '自動修圖';

  @override
  String get modeStyle => '風格';

  @override
  String get modeReference => '參考圖';

  @override
  String get modeManual => '手動調整';

  @override
  String get promptEditFallbackTitle => '指令修圖';

  @override
  String get autoModelExpertHistoryTitle => '專家忠實自動修圖';

  @override
  String get autoModelVividHistoryTitle => '鮮明對比自動修圖';

  @override
  String get referenceEditDisplayTitle => '參考圖修圖';

  @override
  String get manualEditDisplayTitle => '手動調整';

  @override
  String get parserLlm => 'LLM 解析';

  @override
  String get parserRules => '規則解析';

  @override
  String get parserReference => '參考圖模式';

  @override
  String get parserManual => '手動參數';

  @override
  String get styleFamilyNaturalClean => '自然清透';

  @override
  String get styleFamilyPortraitSkin => '人像膚色';

  @override
  String get styleFamilyLandscapeTravel => '風景旅行';

  @override
  String get styleFamilyCinematic => '電影敘事';

  @override
  String get styleFamilyFilmRetro => '底片復古';

  @override
  String get styleFamilyBlackWhite => '黑白';

  @override
  String get styleFamilyNightNeon => '夜景霓虹';

  @override
  String get styleFamilyPastelCreative => '粉彩創意';

  @override
  String get summaryOriginalNewBranch => '原圖 · 下一次修圖會建立新分支';

  @override
  String get summaryChoosePhoto => '選擇圖片後，使用指令或參考圖開始修圖';

  @override
  String get summaryPreviewPrefix => '預覽 · ';

  @override
  String get manualUnavailableNeedPrompt => '請先完成一次指令修圖，再進入手動調整。';

  @override
  String get manualUnavailableReference => '參考圖結果目前不能進入手動調整，請先選擇指令或手動版本。';

  @override
  String get manualUnavailableEngine => '手動調整第一版只支援 OpenCV 結果。';

  @override
  String get manualUnavailableGeneric => '這個版本目前不支援手動調整。';

  @override
  String get statusSelectedNewOriginal => '已選擇新的原始圖片';

  @override
  String get statusReferenceReady => '參考圖已準備完成';

  @override
  String get errorPromptRequired => '請輸入修圖指令。';

  @override
  String get errorReferenceRequired => '請先選擇參考圖片。';

  @override
  String errorStyleCatalogLoad(String error) {
    return '無法載入風格目錄：$error';
  }

  @override
  String get errorOriginalRequired => '請先選擇原始圖片。';

  @override
  String get statusParsingPrompt => '正在解析修圖指令…';

  @override
  String get statusApplyingReference => '正在套用參考圖…';

  @override
  String get statusEditComplete => '修圖完成';

  @override
  String errorEditFailed(String error) {
    return '修圖失敗：$error';
  }

  @override
  String get statusHistorySynced => '歷史紀錄已同步';

  @override
  String get statusSwitchedHistory => '已切換到歷史版本';

  @override
  String get statusSwitchedOriginal => '已切換到原圖，可建立新的歷史分支';

  @override
  String errorOpenManual(String error) {
    return '無法開啟手動調整：$error';
  }

  @override
  String get statusResetSourceParameters => '已回到來源版本參數';

  @override
  String errorManualPreview(String error) {
    return '手動預覽失敗：$error';
  }

  @override
  String get statusApplyingManual => '正在套用手動調整…';

  @override
  String get statusManualCommitted => '手動調整已套用並加入歷史';

  @override
  String errorManualCommit(String error) {
    return '手動調整套用失敗：$error';
  }

  @override
  String get errorStyleAmbiguous => '這段描述同時符合多個風格，請從風格目錄指定一個名稱。';

  @override
  String get errorStyleCompound => '請先套用風格，再用下一句調整亮度、色彩或其他參數。';

  @override
  String get errorStyleAsset => '風格資產或版本驗證失敗，未套用其他替代風格。';

  @override
  String get errorSemanticTargetNotFound => '照片中找不到指定的局部範圍。請換一張圖片，或改用全圖調整。';

  @override
  String get errorAdaptiveClarification => '我不確定要微調哪一項，請補充參數或指定區域。';

  @override
  String get errorAdaptiveConverged => '這個方向的調整已接近目前最小步長；可改用手動參數做最後微調。';

  @override
  String get errorAdaptiveSatisfied => '已保留目前結果，沒有新增重複的歷史版本。';

  @override
  String get errorManualSourceUnsupported => '參考圖結果目前不能手動調整，請選擇指令或手動版本。';

  @override
  String get errorBackendUnavailable => '無法連線到修圖後端，請確認後端已啟動。';

  @override
  String get errorCheckPrompt => '請確認修圖指令後再試一次。';

  @override
  String adaptiveIssuesContext(String message, String contexts) {
    return '$message（涉及：$contexts）';
  }

  @override
  String networkBackendError(String error) {
    return '無法連線到修圖後端：$error';
  }

  @override
  String backendHttpError(int statusCode) {
    return '後端請求失敗（HTTP $statusCode）';
  }

  @override
  String get backendInvalidResponse => '後端回傳了無法辨識的資料格式。';

  @override
  String get photoGitTitle => '版本操作';

  @override
  String get photoGitSubtitle => '合併或選擇性撤銷可追蹤調整';

  @override
  String get photoGitUnavailable => '請先選取一個 OpenCV 歷史版本，再使用版本操作。';

  @override
  String get photoGitManualDraftBlocked => '請先完成或捨棄手動調整草稿，再開始版本操作。';

  @override
  String get photoGitMerge => '版本合併';

  @override
  String get photoGitSelectiveRevert => '選擇性撤銷';

  @override
  String get photoGitDeterministic => '確定性版本計畫';

  @override
  String get photoGitTarget => '目標版本';

  @override
  String get photoGitSource => '來源版本';

  @override
  String get photoGitRevertStep => '待撤銷步驟';

  @override
  String get photoGitChooseSource => '選擇另一個版本';

  @override
  String get photoGitChooseRevertStep => '從目標版本的祖先鏈選擇步驟';

  @override
  String get photoGitInstruction => '操作範圍';

  @override
  String get photoGitMergeHint => '例如：只帶入天空的飽和度';

  @override
  String get photoGitRevertHint => '例如：只撤銷這一步的飽和度';

  @override
  String get photoGitScopeAssist => '可選範圍捷徑';

  @override
  String get photoGitAnyRegion => '不限區域';

  @override
  String get photoGitAnyParameter => '不限參數';

  @override
  String get photoGitAnalyze => '分析變更';

  @override
  String get photoGitAnalyzing => '分析中…';

  @override
  String get photoGitPlanSummary => '計畫摘要';

  @override
  String get photoGitAdded => '將加入';

  @override
  String get photoGitRemoved => '將移除';

  @override
  String get photoGitConflicts => '衝突';

  @override
  String get photoGitNoContribution => '沒有符合範圍的可追蹤變更。';

  @override
  String get photoGitConflictHelp => '所有衝突完成選擇後才能預覽。';

  @override
  String get photoGitKeepTarget => '保留目標版本';

  @override
  String get photoGitUseSource => '採用來源版本';

  @override
  String get photoGitReplayLater => '撤銷後重播後續調整';

  @override
  String get photoGitPreview => '產生預覽';

  @override
  String get photoGitPreviewing => '正在產生預覽…';

  @override
  String get photoGitCommit => '確認建立版本';

  @override
  String get photoGitCommitting => '正在建立版本…';

  @override
  String get photoGitCancel => '取消版本操作';

  @override
  String get photoGitMergedFrom => '合併自';

  @override
  String get photoGitRevertedFrom => '撤銷自';

  @override
  String get photoGitCommonAncestor => '共同祖先';

  @override
  String get photoGitSchema => 'Recipe 版本';

  @override
  String get photoGitPlanHash => '計畫';

  @override
  String get photoGitResolutions => '衝突決策';

  @override
  String get photoGitTargetValue => '目標值';

  @override
  String get photoGitSourceValue => '來源值';

  @override
  String get photoGitLaterChanges => '後續修改';

  @override
  String get statusPhotoGitPlanning => '正在分析版本差異…';

  @override
  String get statusPhotoGitConflictsFound => '發現衝突，請逐項選擇結果。';

  @override
  String get statusPhotoGitNoChange => '所選範圍不會改變目標版本。';

  @override
  String get statusPhotoGitPlanReady => '版本計畫已就緒。';

  @override
  String get statusPhotoGitPreviewing => '正在產生版本預覽…';

  @override
  String get statusPhotoGitPreviewReady => '預覽已就緒，請比較後再建立版本。';

  @override
  String get statusPhotoGitCommitting => '正在建立可追蹤版本…';

  @override
  String get statusPhotoGitCommitted => '版本已加入歷史紀錄。';

  @override
  String get errorPhotoGitRequestIncomplete => '請選擇版本並指定要操作的區域或參數。';

  @override
  String errorPhotoGitPlan(String error) {
    return '版本分析失敗：$error';
  }

  @override
  String errorPhotoGitPreview(String error) {
    return '版本預覽失敗：$error';
  }

  @override
  String errorPhotoGitCommit(String error) {
    return '建立版本失敗：$error';
  }

  @override
  String get errorPhotoGitScope => '請用文字或捷徑指定支援的區域或參數。';

  @override
  String get errorPhotoGitConflict => '仍有版本衝突未決定，請逐項選擇後再試一次。';

  @override
  String get errorPhotoGitStale => '版本內容已變更，請重新分析後再預覽。';

  @override
  String get errorPhotoGitNoChange => '所選內容與目標版本相同，不會建立重複版本。';

  @override
  String get errorPhotoGitUnsupported => '這個版本缺少可安全重算的來源資訊，無法進行此操作。';

  @override
  String get errorPhotoGitDraftActive => '請先完成或取消目前的版本操作。';

  @override
  String contractBadgePassed(int passed, int total) {
    return '合約 $passed/$total 通過';
  }

  @override
  String contractBadgeAdjusted(int scale) {
    return '為符合限制，幅度已調整至 $scale%';
  }

  @override
  String get contractDetailsTitle => '可驗證修圖合約';

  @override
  String get contractStatusPassed => '原要求幅度通過';

  @override
  String get contractStatusAdjusted => '安全縮小幅度後通過';

  @override
  String get contractChecks => '驗證項目';

  @override
  String get contractConstraints => '系統理解的限制';

  @override
  String get contractRequestedScale => '要求幅度';

  @override
  String get contractAppliedScale => '實際幅度';

  @override
  String get contractThreshold => '門檻';

  @override
  String get contractThresholdSource => '門檻來源';

  @override
  String get contractBaseline => '基準值';

  @override
  String get contractActual => '實測值';

  @override
  String get contractMetricVersion => '量測版本';

  @override
  String get contractTargetVersion => '驗證目標';

  @override
  String get contractParentVersion => 'Parent';

  @override
  String get contractVerificationTime => '驗證時間';

  @override
  String get contractVersions => '合約版本';

  @override
  String get contractRequestedParameters => '要求參數';

  @override
  String get contractActualParameters => '實際參數';

  @override
  String get contractPolicyDefault => '版本化預設政策';

  @override
  String get contractExplicitUser => '使用者明確指定';

  @override
  String get contractSystemPolicy => '系統安全政策';

  @override
  String get contractOperatorAtMost => '不得超過';

  @override
  String get contractOperatorNoWorse => '不得比基準更差';

  @override
  String get contractCheckPassed => '通過';

  @override
  String get contractCheckFailed => '未通過';

  @override
  String get contractUnknownMetric => '未知量測項目';

  @override
  String get contractNoChecks => '後端未回傳驗證項目資料。';

  @override
  String contractMilliseconds(String value) {
    return '$value 毫秒';
  }

  @override
  String get errorContractClarification => '有保護條件尚不明確，請補充量測項目、區域或門檻後重試。';

  @override
  String get errorContractUnsupported => '目前無法驗證這項保護條件或照片區域，因此沒有套用修圖。';

  @override
  String get errorContractUnsatisfied => '找不到能同時通過所有保護條件且仍有效果的結果，請調整門檻後重試。';

  @override
  String get errorContractNoChange => '安全結果不會產生可辨識變化，因此沒有新增重複版本。';

  @override
  String get errorContractConflict => '這個請求識別碼已用於不同修圖內容，請重新送出目前指令。';

  @override
  String get errorContractSchema => '無法載入合約顯示定義；仍會安全顯示量測識別碼。';
}

/// The translations for Chinese, as used in Taiwan (`zh_TW`).
class AppLocalizationsZhTw extends AppLocalizationsZh {
  AppLocalizationsZhTw() : super('zh_TW');

  @override
  String get appTitle => 'AI 修圖';

  @override
  String get appCompactTitle => 'AI 修圖';

  @override
  String get languageTraditionalChinese => '繁體中文';

  @override
  String get languageEnglish => '英文';

  @override
  String get switchToTraditionalChinese => '將介面切換為繁體中文';

  @override
  String get switchToEnglish => '將介面切換為英文';

  @override
  String get themeLight => '淺色';

  @override
  String get themeDark => '深色';

  @override
  String get switchToLightTheme => '切換為淺色模式';

  @override
  String get switchToDarkTheme => '切換為深色模式';

  @override
  String get clearCurrentWork => '清除目前工作';

  @override
  String get chooseOriginal => '選擇原圖';

  @override
  String get changeOriginal => '更換原圖';

  @override
  String get toolPrompt => '指令';

  @override
  String get toolAutoModels => '模型';

  @override
  String get toolStyles => '風格';

  @override
  String get toolReference => '參考';

  @override
  String get toolManual => '調整';

  @override
  String get toolHistory => '歷史';

  @override
  String get autoModelTitle => '自動修圖比較';

  @override
  String get autoModelSubtitle => '從目前版本一次建立並保存兩個候選';

  @override
  String get autoModelSource => '共同來源';

  @override
  String get autoModelExpertTitle => '專家忠實';

  @override
  String get autoModelExpertDescription => '較克制、接近所選 Expert C 專家成品的訓練結果。';

  @override
  String get autoModelVividTitle => '鮮明對比';

  @override
  String get autoModelVividDescription => '對比與色彩更強、較明亮鮮豔的另一種詮釋。';

  @override
  String get autoModelRun => '產生兩個候選';

  @override
  String get autoModelRetry => '重試或取回候選';

  @override
  String get autoModelRunning => '正在產生兩個候選…';

  @override
  String get autoModelCancel => '停止等待';

  @override
  String get autoModelSelect => '使用這個版本';

  @override
  String get autoModelSelected => '目前選取';

  @override
  String get autoModelSourceHint => '目前版本會成為兩個候選的共同 parent；成功結果都會永久保留在歷史。';

  @override
  String get autoModelRepeatHint => '這個來源已包含自動修圖；仍可再次執行，但效果可能會加強。';

  @override
  String get autoModelNoSource => '請先選擇原圖或一個已保存版本。';

  @override
  String get autoModelCandidateFailed => '這個候選未完成';

  @override
  String get autoModelCancelledHint =>
      '後端可能仍會完成；再次產生會連回同一請求，既有工作階段也可從歷史取回已保存結果。';

  @override
  String get labelOriginal => '原圖';

  @override
  String get labelCompare => '對比';

  @override
  String get labelResult => '結果';

  @override
  String get labelPreview => '預覽';

  @override
  String get labelBefore => '之前';

  @override
  String get labelAfter => '之後';

  @override
  String get comparisonBaseline => '比較基準';

  @override
  String get comparisonBaselineOriginal => '原圖';

  @override
  String get comparisonBaselineParent => '上一步';

  @override
  String get comparisonParentUnavailable => '這個版本沒有可用的上一步，已改用原圖比較。';

  @override
  String get comparisonDragHandle => '前後對比分割線';

  @override
  String comparisonDragHandleValue(int percent) {
    return '前後對比分割位置 $percent%';
  }

  @override
  String get comparisonMoveLeft => '顯示更多結果';

  @override
  String get comparisonMoveRight => '顯示更多比較基準';

  @override
  String get resetZoom => '重設視角';

  @override
  String get holdToSeeOriginal => '按住照片可暫時查看原圖';

  @override
  String get dismissHint => '關閉提示';

  @override
  String get selectPhotoToStart => '選擇一張照片開始';

  @override
  String get photoWorkspaceDescription => '照片會完整顯示，修圖結果與歷史版本都會保留。';

  @override
  String get photoWorkspaceCompactDescription => '照片、結果與歷史版本都會保留。';

  @override
  String get selectOriginal => '選擇原圖';

  @override
  String get resultAppearsHere => '完成修圖後，結果會顯示在這裡';

  @override
  String get noImage => '尚無圖片';

  @override
  String get processing => '處理中…';

  @override
  String get imageLoadFailed => '圖片載入失敗';

  @override
  String get discardDraftTitle => '捨棄尚未套用的調整？';

  @override
  String get discardDraftForHistory => '切換歷史版本會捨棄目前手動調整草稿。';

  @override
  String get discardDraftForOriginal => '回到原圖建立新分支會捨棄目前手動調整草稿。';

  @override
  String get discardPhotoGitForTool => '開啟其他工具會捨棄目前的版本操作與預覽。';

  @override
  String get actionBack => '返回';

  @override
  String get actionDiscardAndSwitch => '捨棄並切換';

  @override
  String get replaceOriginalTitle => '更換原始圖片？';

  @override
  String get replaceOriginalMessage => '更換後會清除目前 session、未套用的手動草稿與未完成的版本操作。';

  @override
  String get actionCancel => '取消';

  @override
  String get actionReplaceImage => '更換圖片';

  @override
  String imagePickFailed(String error) {
    return '無法選擇圖片：$error';
  }

  @override
  String get clearWorkTitle => '清除目前工作？';

  @override
  String get clearWorkMessage => '畫面會回到初始狀態並捨棄未完成的草稿，後端已保存的歷史不會被刪除。';

  @override
  String get actionClearScreen => '清除畫面';

  @override
  String get promptEditTitle => '指令修圖';

  @override
  String get promptBranchFromOriginal => '從原圖建立新的歷史分支';

  @override
  String get promptFirstVersionFromOriginal => '從原圖建立第一個版本';

  @override
  String get promptContinueSelected => '從目前選中的版本繼續調整';

  @override
  String get promptHint => '例如：亮度加十、套用百分百經典電影感，或合併版本四和版本六';

  @override
  String get promptModeNotice => '一次輸入或口說一個動作；套用後會自動分流到修圖、精確調參、風格或版本工具。';

  @override
  String get commandPlanning => '正在理解指令…';

  @override
  String get commandPlanTitle => '指令執行計畫';

  @override
  String get commandPreviewNotice => '版本操作一定先產生預覽，只有你確認後才會建立新版本。';

  @override
  String get speechLanguageLabel => '辨識語言';

  @override
  String get speechLanguageHelp => '短指令請明確選中文或英文；中英混用再使用自動偵測。';

  @override
  String get speechLanguageTraditionalChinese => '繁體中文';

  @override
  String get speechLanguageEnglish => 'English';

  @override
  String get speechLanguageAutomatic => '自動偵測';

  @override
  String speechResultMetadata(String language, String model) {
    return '辨識為 $language · $model';
  }

  @override
  String get speechStart => '使用麥克風';

  @override
  String get speechStop => '停止';

  @override
  String get speechCancel => '取消';

  @override
  String get speechRequestingPermission => '正在請求麥克風權限…';

  @override
  String speechRecordingSeconds(int seconds) {
    return '錄音中 · $seconds 秒';
  }

  @override
  String get speechTranscribing => '正在將語音轉成可編輯文字…';

  @override
  String get speechPrivacyNotice => '音訊只交給本機後端處理，不會寫入修圖歷史。';

  @override
  String get speechUnavailable => '目前無法使用麥克風輸入，仍可直接輸入文字指令。';

  @override
  String get statusSpeechCompleted => '語音已加入可編輯文字，確認內容後再套用指令。';

  @override
  String get statusSpeechCancelled => '已取消這次語音輸入。';

  @override
  String get errorSpeechPermissionDenied => '麥克風權限被拒絕，請在 Chrome 設定中允許，或改用文字輸入。';

  @override
  String get errorSpeechNoMicrophone => '找不到可用的麥克風，請檢查裝置，或改用文字輸入。';

  @override
  String get errorSpeechRecorderUnavailable =>
      '瀏覽器無法提供需要的錄音格式，請使用目前版本的 Chrome，或改用文字輸入。';

  @override
  String get errorSpeechRecordingFailed => '錄音失敗，請檢查麥克風後重試。';

  @override
  String get errorSpeechNoAudio => '麥克風沒有回傳可用音訊，請重新錄音。';

  @override
  String get errorSpeechInvalidAudio => '無法讀取這段錄音，請重新錄音。';

  @override
  String get errorSpeechUnsupportedFormat => '目前不支援這段錄音格式，請在 Chrome 重新錄音。';

  @override
  String get errorSpeechNoSpeech => '沒有偵測到有效語音，請靠近麥克風後重試。';

  @override
  String get errorSpeechTooLong => '錄音超過 15 秒，請改說較短的修圖指令。';

  @override
  String get errorSpeechTooLarge => '錄音檔太大，請改說較短的修圖指令。';

  @override
  String get errorSpeechModelUnavailable => '本機語音模型目前無法使用，請檢查後端模型與裝置設定。';

  @override
  String get errorSpeechTranscriptionFailed => '語音辨識失敗，請重新錄音。';

  @override
  String get errorSpeechTimeout => '語音辨識等待過久，請重新嘗試。';

  @override
  String get errorSpeechBackendUnavailable => '無法連線到本機語音後端，仍可使用文字輸入。';

  @override
  String get applyPrompt => '套用指令';

  @override
  String get styleCatalogTitle => '風格目錄';

  @override
  String get styleCatalogUnavailable => '風格目錄目前無法載入，請確認後端已啟動。';

  @override
  String styleCatalogSubtitle(int count, String version) {
    return '$count 種已核准風格 · v$version';
  }

  @override
  String get styleCategoryPrevious => '向左查看更多分類';

  @override
  String get styleCategoryNext => '向右查看更多分類';

  @override
  String get styleCategoryAll => '全部';

  @override
  String get styleStrength => '強度';

  @override
  String get applyStyle => '套用風格';

  @override
  String get referenceEditTitle => '參考圖修圖';

  @override
  String get referenceFromOriginal => '原圖會依參考圖的色彩方向調整';

  @override
  String get referenceFromCurrent => '從目前版本套用參考圖方向';

  @override
  String get selectReference => '選擇參考圖';

  @override
  String get changeReference => '更換參考圖';

  @override
  String get removeReference => '移除參考圖';

  @override
  String get applyReference => '套用參考圖';

  @override
  String get manualEditTitle => '手動調整';

  @override
  String manualSourceVersion(String target, String mode) {
    return '來源版本 · $target · $mode';
  }

  @override
  String get advancedAdjustments => '進階調整';

  @override
  String get historyTitle => '歷史紀錄';

  @override
  String historyVersionCount(int count) {
    return '版本 $count';
  }

  @override
  String get refreshHistory => '重新同步';

  @override
  String get selectedOriginalNewBranch => '已選原圖 · 下一次建立新分支';

  @override
  String get createBranchFromOriginal => '從原圖建立新分支';

  @override
  String get emptyHistory => '完成第一次修圖後，版本會依序顯示在這裡。';

  @override
  String get currentPreview => '目前預覽';

  @override
  String get currentAdjustments => '目前調整';

  @override
  String styleEffectiveParameters(int strength) {
    return '以下為依 $strength% 強度折算的等效參數；風格實際效果也包含曲線、分色與其他內部配方。';
  }

  @override
  String get noManualParameters => '這個版本沒有可顯示的手動參數。';

  @override
  String styleUnderstanding(String name, int strength) {
    return '理解結果：已套用 $name，強度 $strength%。';
  }

  @override
  String adjustmentCount(int count) {
    return '$count 項微調';
  }

  @override
  String get adaptiveIntervalReset => '已重設區間';

  @override
  String get adaptiveConverged => '已收斂';

  @override
  String get adaptiveContinue => '持續微調';

  @override
  String get adaptiveFineTune => '自適應微調';

  @override
  String get relativeAdjustment => '本次相對調整';

  @override
  String get candidateValue => '候選值';

  @override
  String get currentBounds => '目前界線';

  @override
  String get stepSize => '步幅';

  @override
  String stepSizeWithTransform(String transform) {
    return '步幅（$transform）';
  }

  @override
  String get adaptiveReasonInitial => '建立初始步幅';

  @override
  String get adaptiveReasonReverse => '從目前效果往回收斂';

  @override
  String get adaptiveReasonHandoff => '接手相關參數調整';

  @override
  String get adaptiveReasonMidpoint => '依回饋取區間中點';

  @override
  String get adaptiveReasonContinue => '延續同方向探索';

  @override
  String get adaptiveReasonNarrow => '依反向回饋縮小';

  @override
  String get adaptiveReasonReanchor => '重新建立調整基準';

  @override
  String get adaptiveReasonAbsolute => '採用明確數值並重設區間';

  @override
  String get adaptiveReasonRelative => '依相對數值調整';

  @override
  String get adaptiveReasonResetAxis => '重設單一參數';

  @override
  String get adaptiveReasonResetOriginal => '回到原圖';

  @override
  String get collapse => '收合';

  @override
  String resetParameter(String label) {
    return '將$label設為中性值';
  }

  @override
  String equivalentParameters(String summary) {
    return '等效 $summary';
  }

  @override
  String historyVersionMode(int version, String mode) {
    return '版本 $version · $mode';
  }

  @override
  String get rootBranch => '根分支';

  @override
  String get continuesParent => '接續父版本';

  @override
  String continuesVersion(int version) {
    return '接續版本 $version';
  }

  @override
  String get referenceNotSelected => '尚未選擇參考圖';

  @override
  String get actionReset => '重設';

  @override
  String get actionApply => '套用';

  @override
  String get actionApplying => '套用中…';

  @override
  String get notApplied => '尚未套用';

  @override
  String get parameterExposure => '曝光';

  @override
  String get parameterBrightness => '亮度';

  @override
  String get parameterContrast => '對比';

  @override
  String get parameterHighlights => '高光';

  @override
  String get parameterShadows => '陰影';

  @override
  String get parameterWhites => '白位';

  @override
  String get parameterBlacks => '黑位';

  @override
  String get parameterSaturation => '飽和度';

  @override
  String get parameterVibrance => '自然飽和度';

  @override
  String get parameterTemperature => '色溫';

  @override
  String get parameterWhiteBalanceTint => '白平衡色偏';

  @override
  String get parameterSharpen => '銳化';

  @override
  String get parameterClarity => '清晰度';

  @override
  String get parameterDehaze => '去霧';

  @override
  String get parameterVignette => '暗角';

  @override
  String get regionAll => '全圖';

  @override
  String get regionSky => '天空';

  @override
  String get regionPerson => '人物';

  @override
  String get regionBackground => '背景';

  @override
  String get regionHighlights => '亮部';

  @override
  String get regionShadows => '暗部';

  @override
  String get regionCenter => '中央';

  @override
  String get regionEdges => '邊緣';

  @override
  String get modePrompt => '指令';

  @override
  String get modeAutoModel => '自動修圖';

  @override
  String get modeStyle => '風格';

  @override
  String get modeReference => '參考圖';

  @override
  String get modeManual => '手動調整';

  @override
  String get promptEditFallbackTitle => '指令修圖';

  @override
  String get autoModelExpertHistoryTitle => '專家忠實自動修圖';

  @override
  String get autoModelVividHistoryTitle => '鮮明對比自動修圖';

  @override
  String get referenceEditDisplayTitle => '參考圖修圖';

  @override
  String get manualEditDisplayTitle => '手動調整';

  @override
  String get parserLlm => 'LLM 解析';

  @override
  String get parserRules => '規則解析';

  @override
  String get parserReference => '參考圖模式';

  @override
  String get parserManual => '手動參數';

  @override
  String get styleFamilyNaturalClean => '自然清透';

  @override
  String get styleFamilyPortraitSkin => '人像膚色';

  @override
  String get styleFamilyLandscapeTravel => '風景旅行';

  @override
  String get styleFamilyCinematic => '電影敘事';

  @override
  String get styleFamilyFilmRetro => '底片復古';

  @override
  String get styleFamilyBlackWhite => '黑白';

  @override
  String get styleFamilyNightNeon => '夜景霓虹';

  @override
  String get styleFamilyPastelCreative => '粉彩創意';

  @override
  String get summaryOriginalNewBranch => '原圖 · 下一次修圖會建立新分支';

  @override
  String get summaryChoosePhoto => '選擇圖片後，使用指令或參考圖開始修圖';

  @override
  String get summaryPreviewPrefix => '預覽 · ';

  @override
  String get manualUnavailableNeedPrompt => '請先完成一次指令修圖，再進入手動調整。';

  @override
  String get manualUnavailableReference => '參考圖結果目前不能進入手動調整，請先選擇指令或手動版本。';

  @override
  String get manualUnavailableEngine => '手動調整第一版只支援 OpenCV 結果。';

  @override
  String get manualUnavailableGeneric => '這個版本目前不支援手動調整。';

  @override
  String get statusSelectedNewOriginal => '已選擇新的原始圖片';

  @override
  String get statusReferenceReady => '參考圖已準備完成';

  @override
  String get errorPromptRequired => '請輸入修圖指令。';

  @override
  String get errorReferenceRequired => '請先選擇參考圖片。';

  @override
  String errorStyleCatalogLoad(String error) {
    return '無法載入風格目錄：$error';
  }

  @override
  String get errorOriginalRequired => '請先選擇原始圖片。';

  @override
  String get statusParsingPrompt => '正在解析修圖指令…';

  @override
  String get statusApplyingReference => '正在套用參考圖…';

  @override
  String get statusEditComplete => '修圖完成';

  @override
  String errorEditFailed(String error) {
    return '修圖失敗：$error';
  }

  @override
  String get statusHistorySynced => '歷史紀錄已同步';

  @override
  String get statusSwitchedHistory => '已切換到歷史版本';

  @override
  String get statusSwitchedOriginal => '已切換到原圖，可建立新的歷史分支';

  @override
  String errorOpenManual(String error) {
    return '無法開啟手動調整：$error';
  }

  @override
  String get statusResetSourceParameters => '已回到來源版本參數';

  @override
  String errorManualPreview(String error) {
    return '手動預覽失敗：$error';
  }

  @override
  String get statusApplyingManual => '正在套用手動調整…';

  @override
  String get statusManualCommitted => '手動調整已套用並加入歷史';

  @override
  String errorManualCommit(String error) {
    return '手動調整套用失敗：$error';
  }

  @override
  String get errorStyleAmbiguous => '這段描述同時符合多個風格，請從風格目錄指定一個名稱。';

  @override
  String get errorStyleCompound => '請先套用風格，再用下一句調整亮度、色彩或其他參數。';

  @override
  String get errorStyleAsset => '風格資產或版本驗證失敗，未套用其他替代風格。';

  @override
  String get errorSemanticTargetNotFound => '照片中找不到指定的局部範圍。請換一張圖片，或改用全圖調整。';

  @override
  String get errorAdaptiveClarification => '我不確定要微調哪一項，請補充參數或指定區域。';

  @override
  String get errorAdaptiveConverged => '這個方向的調整已接近目前最小步長；可改用手動參數做最後微調。';

  @override
  String get errorAdaptiveSatisfied => '已保留目前結果，沒有新增重複的歷史版本。';

  @override
  String get errorManualSourceUnsupported => '參考圖結果目前不能手動調整，請選擇指令或手動版本。';

  @override
  String get errorBackendUnavailable => '無法連線到修圖後端，請確認後端已啟動。';

  @override
  String get errorCheckPrompt => '請確認修圖指令後再試一次。';

  @override
  String adaptiveIssuesContext(String message, String contexts) {
    return '$message（涉及：$contexts）';
  }

  @override
  String networkBackendError(String error) {
    return '無法連線到修圖後端：$error';
  }

  @override
  String backendHttpError(int statusCode) {
    return '後端請求失敗（HTTP $statusCode）';
  }

  @override
  String get backendInvalidResponse => '後端回傳了無法辨識的資料格式。';

  @override
  String get photoGitTitle => '版本操作';

  @override
  String get photoGitSubtitle => '合併或選擇性撤銷可追蹤調整';

  @override
  String get photoGitUnavailable => '請先選取一個 OpenCV 歷史版本，再使用版本操作。';

  @override
  String get photoGitManualDraftBlocked => '請先完成或捨棄手動調整草稿，再開始版本操作。';

  @override
  String get photoGitMerge => '版本合併';

  @override
  String get photoGitSelectiveRevert => '選擇性撤銷';

  @override
  String get photoGitDeterministic => '確定性版本計畫';

  @override
  String get photoGitTarget => '目標版本';

  @override
  String get photoGitSource => '來源版本';

  @override
  String get photoGitRevertStep => '待撤銷步驟';

  @override
  String get photoGitChooseSource => '選擇另一個版本';

  @override
  String get photoGitChooseRevertStep => '從目標版本的祖先鏈選擇步驟';

  @override
  String get photoGitInstruction => '操作範圍';

  @override
  String get photoGitMergeHint => '例如：只帶入天空的飽和度';

  @override
  String get photoGitRevertHint => '例如：只撤銷這一步的飽和度';

  @override
  String get photoGitScopeAssist => '可選範圍捷徑';

  @override
  String get photoGitAnyRegion => '不限區域';

  @override
  String get photoGitAnyParameter => '不限參數';

  @override
  String get photoGitAnalyze => '分析變更';

  @override
  String get photoGitAnalyzing => '分析中…';

  @override
  String get photoGitPlanSummary => '計畫摘要';

  @override
  String get photoGitAdded => '將加入';

  @override
  String get photoGitRemoved => '將移除';

  @override
  String get photoGitConflicts => '衝突';

  @override
  String get photoGitNoContribution => '沒有符合範圍的可追蹤變更。';

  @override
  String get photoGitConflictHelp => '所有衝突完成選擇後才能預覽。';

  @override
  String get photoGitKeepTarget => '保留目標版本';

  @override
  String get photoGitUseSource => '採用來源版本';

  @override
  String get photoGitReplayLater => '撤銷後重播後續調整';

  @override
  String get photoGitPreview => '產生預覽';

  @override
  String get photoGitPreviewing => '正在產生預覽…';

  @override
  String get photoGitCommit => '確認建立版本';

  @override
  String get photoGitCommitting => '正在建立版本…';

  @override
  String get photoGitCancel => '取消版本操作';

  @override
  String get photoGitMergedFrom => '合併自';

  @override
  String get photoGitRevertedFrom => '撤銷自';

  @override
  String get photoGitCommonAncestor => '共同祖先';

  @override
  String get photoGitSchema => 'Recipe 版本';

  @override
  String get photoGitPlanHash => '計畫';

  @override
  String get photoGitResolutions => '衝突決策';

  @override
  String get photoGitTargetValue => '目標值';

  @override
  String get photoGitSourceValue => '來源值';

  @override
  String get photoGitLaterChanges => '後續修改';

  @override
  String get statusPhotoGitPlanning => '正在分析版本差異…';

  @override
  String get statusPhotoGitConflictsFound => '發現衝突，請逐項選擇結果。';

  @override
  String get statusPhotoGitNoChange => '所選範圍不會改變目標版本。';

  @override
  String get statusPhotoGitPlanReady => '版本計畫已就緒。';

  @override
  String get statusPhotoGitPreviewing => '正在產生版本預覽…';

  @override
  String get statusPhotoGitPreviewReady => '預覽已就緒，請比較後再建立版本。';

  @override
  String get statusPhotoGitCommitting => '正在建立可追蹤版本…';

  @override
  String get statusPhotoGitCommitted => '版本已加入歷史紀錄。';

  @override
  String get errorPhotoGitRequestIncomplete => '請選擇版本並指定要操作的區域或參數。';

  @override
  String errorPhotoGitPlan(String error) {
    return '版本分析失敗：$error';
  }

  @override
  String errorPhotoGitPreview(String error) {
    return '版本預覽失敗：$error';
  }

  @override
  String errorPhotoGitCommit(String error) {
    return '建立版本失敗：$error';
  }

  @override
  String get errorPhotoGitScope => '請用文字或捷徑指定支援的區域或參數。';

  @override
  String get errorPhotoGitConflict => '仍有版本衝突未決定，請逐項選擇後再試一次。';

  @override
  String get errorPhotoGitStale => '版本內容已變更，請重新分析後再預覽。';

  @override
  String get errorPhotoGitNoChange => '所選內容與目標版本相同，不會建立重複版本。';

  @override
  String get errorPhotoGitUnsupported => '這個版本缺少可安全重算的來源資訊，無法進行此操作。';

  @override
  String get errorPhotoGitDraftActive => '請先完成或取消目前的版本操作。';

  @override
  String contractBadgePassed(int passed, int total) {
    return '合約 $passed/$total 通過';
  }

  @override
  String contractBadgeAdjusted(int scale) {
    return '為符合限制，幅度已調整至 $scale%';
  }

  @override
  String get contractDetailsTitle => '可驗證修圖合約';

  @override
  String get contractStatusPassed => '原要求幅度通過';

  @override
  String get contractStatusAdjusted => '安全縮小幅度後通過';

  @override
  String get contractChecks => '驗證項目';

  @override
  String get contractConstraints => '系統理解的限制';

  @override
  String get contractRequestedScale => '要求幅度';

  @override
  String get contractAppliedScale => '實際幅度';

  @override
  String get contractThreshold => '門檻';

  @override
  String get contractThresholdSource => '門檻來源';

  @override
  String get contractBaseline => '基準值';

  @override
  String get contractActual => '實測值';

  @override
  String get contractMetricVersion => '量測版本';

  @override
  String get contractTargetVersion => '驗證目標';

  @override
  String get contractParentVersion => 'Parent';

  @override
  String get contractVerificationTime => '驗證時間';

  @override
  String get contractVersions => '合約版本';

  @override
  String get contractRequestedParameters => '要求參數';

  @override
  String get contractActualParameters => '實際參數';

  @override
  String get contractPolicyDefault => '版本化預設政策';

  @override
  String get contractExplicitUser => '使用者明確指定';

  @override
  String get contractSystemPolicy => '系統安全政策';

  @override
  String get contractOperatorAtMost => '不得超過';

  @override
  String get contractOperatorNoWorse => '不得比基準更差';

  @override
  String get contractCheckPassed => '通過';

  @override
  String get contractCheckFailed => '未通過';

  @override
  String get contractUnknownMetric => '未知量測項目';

  @override
  String get contractNoChecks => '後端未回傳驗證項目資料。';

  @override
  String contractMilliseconds(String value) {
    return '$value 毫秒';
  }

  @override
  String get errorContractClarification => '有保護條件尚不明確，請補充量測項目、區域或門檻後重試。';

  @override
  String get errorContractUnsupported => '目前無法驗證這項保護條件或照片區域，因此沒有套用修圖。';

  @override
  String get errorContractUnsatisfied => '找不到能同時通過所有保護條件且仍有效果的結果，請調整門檻後重試。';

  @override
  String get errorContractNoChange => '安全結果不會產生可辨識變化，因此沒有新增重複版本。';

  @override
  String get errorContractConflict => '這個請求識別碼已用於不同修圖內容，請重新送出目前指令。';

  @override
  String get errorContractSchema => '無法載入合約顯示定義；仍會安全顯示量測識別碼。';
}
