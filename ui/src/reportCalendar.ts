export const DAY_MS = 86_400_000;
const IST_OFFSET_MS = 19_800_000;
export const istDateValue = (value: Date) => new Date(value.getTime() + IST_OFFSET_MS).toISOString().slice(0, 10);
export const istMidnight = (value: string) => new Date(`${value}T00:00:00+05:30`);
export const startOfIstDay = (value: Date) => istMidnight(istDateValue(value));
export const moveIstDay = (value: Date, days: number) => new Date(value.getTime() + days * DAY_MS);
export const formatIst = (value: string) => new Date(value).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const previousIstDay = (now: Date = new Date()) => moveIstDay(startOfIstDay(now), -1);
export const containingSaturday = (value: string) => {
  const weekday = new Date(`${value}T00:00:00Z`).getUTCDay();
  return istDateValue(moveIstDay(istMidnight(value), -((weekday + 1) % 7)));
};
export const previousCompletedWeek = (now: Date = new Date()) => moveIstDay(istMidnight(containingSaturday(istDateValue(now))), -7);
