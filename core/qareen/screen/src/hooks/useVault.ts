import { useQuery } from '@tanstack/react-query';
import type {
  VaultCollectionListResponse,
  VaultSearchResponse,
  VaultFileResponse,
} from '@/lib/types';

const API = '/api';

async function fetchCollections(): Promise<VaultCollectionListResponse> {
  const res = await fetch(`${API}/vault/collections`);
  if (!res.ok) throw new Error(`Vault collections error: ${res.status}`);
  return res.json();
}

async function searchVault(query: string, collection?: string): Promise<VaultSearchResponse> {
  let url = `${API}/vault/search?q=${encodeURIComponent(query)}`;
  if (collection) url += `&collection=${encodeURIComponent(collection)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Vault search error: ${res.status}`);
  return res.json();
}

async function fetchFile(path: string): Promise<VaultFileResponse> {
  const res = await fetch(`${API}/vault/file/${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Vault file error: ${res.status}`);
  return res.json();
}

export function useVaultCollections() {
  return useQuery({
    queryKey: ['vault-collections'],
    queryFn: fetchCollections,
    staleTime: 60_000,
  });
}

export function useVaultSearch(query: string, collection?: string) {
  return useQuery({
    queryKey: ['vault-search', query, collection ?? ''],
    queryFn: () => searchVault(query, collection),
    enabled: query.length >= 2,
  });
}

export function useVaultFile(path: string | null) {
  return useQuery({
    queryKey: ['vault-file', path],
    queryFn: () => fetchFile(path!),
    enabled: !!path,
  });
}
