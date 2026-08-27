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
  String get toolStyles => '風格';

  @override
  String get toolReference => '參考';

  @override
  String get toolManual => '調整';

  @override
  String get toolHistory => '歷史';

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
  String get actionBack => '返回';

  @override
  String get actionDiscardAndSwitch => '捨棄並切換';

  @override
  String get replaceOriginalTitle => '更換原始圖片？';

  @override
  String get replaceOriginalMessage => '更換後會清除目前 session 與未套用的手動草稿。';

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
  String get clearWorkMessage => '畫面會回到初始狀態，後端已保存的歷史不會被刪除。';

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
  String get promptHint => '例如：人物亮一點、天空暗一點、不要太鮮豔';

  @override
  String get promptModeNotice => '指令與參考圖是兩種獨立模式；這裡只會送出文字。';

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
  String get modeStyle => '風格';

  @override
  String get modeReference => '參考圖';

  @override
  String get modeManual => '手動調整';

  @override
  String get promptEditFallbackTitle => '指令修圖';

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
  String get toolStyles => '風格';

  @override
  String get toolReference => '參考';

  @override
  String get toolManual => '調整';

  @override
  String get toolHistory => '歷史';

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
  String get actionBack => '返回';

  @override
  String get actionDiscardAndSwitch => '捨棄並切換';

  @override
  String get replaceOriginalTitle => '更換原始圖片？';

  @override
  String get replaceOriginalMessage => '更換後會清除目前 session 與未套用的手動草稿。';

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
  String get clearWorkMessage => '畫面會回到初始狀態，後端已保存的歷史不會被刪除。';

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
  String get promptHint => '例如：人物亮一點、天空暗一點、不要太鮮豔';

  @override
  String get promptModeNotice => '指令與參考圖是兩種獨立模式；這裡只會送出文字。';

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
  String get modeStyle => '風格';

  @override
  String get modeReference => '參考圖';

  @override
  String get modeManual => '手動調整';

  @override
  String get promptEditFallbackTitle => '指令修圖';

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
}
