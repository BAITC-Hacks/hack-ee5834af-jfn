export type Language = 'kk' | 'en';

export const copy = {
  en: {
    dashboard: 'Dashboard', replay: 'Replay', runs: 'Runs', models: 'Models', auditLog: 'Audit log', operations: 'Operations', systemAvailable: 'System available',
    subtitle: 'Forecast replay and operational trace', backend: 'Backend', agent: 'Agent', local: 'local', sample: 'sample', queued: 'queued', running: 'running', failed: 'failed', error: 'error', idle: 'idle', succeeded: 'succeeded',
    newReplay: 'New replay', chooseWindow: 'Choose the forecast origin and horizon.', timezone: 'Almaty · UTC+5', replayDate: 'Replay date & time', horizon: 'Horizon', dataSource: 'Data source', api: 'API', runForecast: 'Run forecast', runningLabel: 'Running…',
    sampleData: 'Sample data', sampleNotice: 'Displayed values are for interface preview only.', loadError: 'Forecast could not be loaded', noFallback: 'No sample data was substituted.', retry: 'Retry',
    modelVersion: 'Model version', weatherAge: 'Weather age', scadaFreshness: 'SCADA freshness', temporalValidation: 'Temporal validation', unavailable: 'Unavailable', loading: 'Loading…', empty: 'Choose a source and run a forecast to inspect its evidence.',
    powerForecast: 'Power forecast', normalizedOutput: 'Normalized output · Turbine 1 and Turbine 2', hours: 'hours', sampleChart: 'Sample data. ', intervalsUnavailable: 'Actuals and confidence intervals are unavailable.', turbine1: 'Turbine 1', turbine2: 'Turbine 2', normalizedPower: 'Normalized power',
    agentActivity: 'Pipeline activity', toolsUsed: 'Worker lifecycle events for this run', complete: 'Complete', done: 'Done', warning: 'Warning', runDetails: 'Run details', detailsSubtitle: 'Inputs, validation, and revision history', warnings: 'Warnings', revisionHistory: 'Revision history', parent: 'Parent', firstRevision: 'First revision', noActiveRun: 'No active run', sampleRun: 'Sample run',
    english: 'English', kazakh: 'Қазақша', language: 'Language',
  },
  kk: {
    dashboard: 'Бақылау тақтасы', replay: 'Қайта ойнату', runs: 'Іске қосулар', models: 'Модельдер', auditLog: 'Аудит журналы', operations: 'Операциялар', systemAvailable: 'Жүйе қолжетімді',
    subtitle: 'Болжамды қайта ойнату және орындалу тарихы', backend: 'Сервер', agent: 'Агент', local: 'жергілікті', sample: 'үлгі', queued: 'кезекте', running: 'орындалуда', failed: 'сәтсіз', error: 'қате', idle: 'күту', succeeded: 'аяқталды',
    newReplay: 'Жаңа есептеу', chooseWindow: 'Болжамның басталу уақытын және көкжиегін таңдаңыз.', timezone: 'Алматы · UTC+5', replayDate: 'Күні мен уақыты', horizon: 'Көкжиек', dataSource: 'Дерек көзі', api: 'API', runForecast: 'Болжамды іске қосу', runningLabel: 'Орындалуда…',
    sampleData: 'Үлгі деректер', sampleNotice: 'Көрсетілген мәндер интерфейсті алдын ала қарауға арналған.', loadError: 'Болжам жүктелмеді', noFallback: 'Үлгі деректермен алмастырылған жоқ.', retry: 'Қайталау',
    modelVersion: 'Модель нұсқасы', weatherAge: 'Ауа райы деректерінің жасы', scadaFreshness: 'SCADA өзектілігі', temporalValidation: 'Уақыттық тексеру', unavailable: 'Қолжетімсіз', loading: 'Жүктелуде…', empty: 'Дерек көзін таңдап, болжамды іске қосыңыз.',
    powerForecast: 'Қуат болжамы', normalizedOutput: 'Нормаланған қуат · 1 және 2 турбина', hours: 'сағат', sampleChart: 'Үлгі деректер. ', intervalsUnavailable: 'Нақты мәндер мен сенімділік аралықтары қолжетімсіз.', turbine1: '1-турбина', turbine2: '2-турбина', normalizedPower: 'Нормаланған қуат',
    agentActivity: 'Агент әрекеттері', toolsUsed: 'Осы іске қосуда пайдаланылған құралдар', complete: 'Аяқталды', done: 'Дайын', warning: 'Ескерту', runDetails: 'Іске қосу мәліметтері', detailsSubtitle: 'Кірістер, тексерулер және нұсқалар тарихы', warnings: 'Ескертулер', revisionHistory: 'Нұсқалар тарихы', parent: 'Алдыңғы нұсқа', firstRevision: 'Бірінші нұсқа', noActiveRun: 'Белсенді іске қосу жоқ', sampleRun: 'Үлгі іске қосу',
    english: 'English', kazakh: 'Қазақша', language: 'Тіл',
  },
} as const;

export type Copy = { [K in keyof typeof copy.en]: string };
