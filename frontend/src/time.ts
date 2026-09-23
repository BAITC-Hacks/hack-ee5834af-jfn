export function formatForecastTime(value: string) {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Almaty', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
}
