/** Set only by the live/Pages build; development and local production builds keep the full tools. */
export const PUBLIC_RELEASE = import.meta.env.VITE_PUBLIC_RELEASE === '1';
