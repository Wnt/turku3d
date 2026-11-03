import { isLocalHost } from '../../core/localOnly';

/** The COPC product: same-origin through the dev server's proxy (vite.config.ts) or a share server's /pointcloud
 *  mount, else, on a local origin only, osgallery-dev's LAN server (a public page can never reach it). */
export const SOURCES = ['./pointcloud/', 'http://192.168.1.114:7960/'] as const;

export const pointCloudSources = (hostname: string): readonly string[] => isLocalHost(hostname) ? SOURCES : SOURCES.slice(0, 1);
