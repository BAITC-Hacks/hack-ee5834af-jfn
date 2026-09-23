import type { Language } from './i18n';

export function formatForecastTime(value: string, language: Language = 'en') {
  return new Intl.DateTimeFormat(language === 'kk' ? 'kk-KZ' : 'en-GB', { timeZone: 'Asia/Almaty', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
}
