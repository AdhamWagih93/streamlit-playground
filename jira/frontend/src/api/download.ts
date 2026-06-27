import { api } from './client';

// Downloads run through the axios `api` client so the Bearer token is injected
// (a plain <a href> can't carry auth). The response is fetched as a blob and a
// temporary object URL is used to trigger a browser download.
export async function downloadExport(
  url: string,
  params: Record<string, string>,
  fallbackName: string
): Promise<void> {
  const res = await api.get(url, { params, responseType: 'blob' });
  // Derive filename from Content-Disposition if present, else fallbackName.
  const cd = (res.headers['content-disposition'] as string) || '';
  const m = /filename="?([^"]+)"?/.exec(cd);
  const name = m ? m[1] : fallbackName;
  const blobUrl = URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(blobUrl);
}
