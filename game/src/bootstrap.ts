import { isolatePage } from './core/isolation';
import { installCityFetch } from './core/city';
import { sessionStore } from './core/storage';
import { announceArrival } from './gameplay/travel';
// A host whose root is a start page (VITE_START_PAGE, a file of the build: the lab's list of locations of the new
// world, tools/share/publish-w2.sh) shows it when the game is opened without any parameter.
const startPage = import.meta.env.VITE_START_PAGE as string | undefined;
if (startPage && !location.search && !location.hash) location.replace(new URL(startPage, location.href));
await isolatePage();
// Another city's tiles are mounted under its own path on this origin (core/city.ts); the boot screen names the trip.
installCityFetch();
announceArrival(sessionStore());
await import('./main');
