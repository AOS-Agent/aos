import { useQuery } from '@tanstack/react-query';
import type { ChannelListResponse } from '@/lib/types';

const API = '/api';

export function useChannels() {
  return useQuery({
    queryKey: ['channels'],
    queryFn: async (): Promise<ChannelListResponse> => {
      const res = await fetch(`${API}/channels`);
      if (!res.ok) throw new Error(`Channels API error: ${res.status}`);
      return res.json();
    },
    refetchInterval: 60_000,
  });
}
