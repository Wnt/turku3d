import { isolatePage } from './core/isolation';
import { installCityFetch } from './core/city';
import { sessionStore } from './core/storage';
import { announceArrival } from './gameplay/travel';
await isolatePage();
// Another city's tiles are mounted under its own path on this origin (core/city.ts); the boot screen names the trip.
installCityFetch();
announceArrival(sessionStore());
await import('./main');
