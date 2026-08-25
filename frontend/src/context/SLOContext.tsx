/**
 * SLO Context — fetches /admin/slo once at app startup and shares the result
 * across all consumers (Header, AuditSecurityPage) without duplicate requests.
 *
 * This is the SINGLE source of truth for live SLO status in the frontend.
 * It NEVER falls back silently to a "healthy" mock — if the API fails,
 * `sloError` is set and callers must show an honest unavailable state.
 */
import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { SLOResponseSchema } from '../types/api';
import { apiClient } from '../api/client';

interface SLOContextType {
  /** null while loading */
  sloData: SLOResponseSchema | null;
  /** true only on initial load */
  sloLoading: boolean;
  /** non-null when the last fetch attempt failed */
  sloError: string | null;
  /** call to re-fetch from the backend */
  refetchSLO: () => void;
  /** convenience: true when sloData.overall_status === 'HEALTHY' */
  isHealthy: boolean;
  /** convenience: number of active breaches (0 when loading/error) */
  breachCount: number;
}

const SLOContext = createContext<SLOContextType | undefined>(undefined);

export const SLOProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [sloData, setSloData] = useState<SLOResponseSchema | null>(null);
  const [sloLoading, setSloLoading] = useState(true);
  const [sloError, setSloError] = useState<string | null>(null);

  const fetchSLO = useCallback(async () => {
    setSloLoading(true);
    setSloError(null);
    try {
      const data = await apiClient.getSLOStatus();
      setSloData(data);
    } catch (err: any) {
      // DO NOT fall back to a fake "healthy" value.
      // Callers must render an explicit "unavailable" state.
      setSloError(err?.message || 'Failed to load SLO status from /admin/slo');
      setSloData(null);
    } finally {
      setSloLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSLO();
  }, [fetchSLO]);

  const isHealthy =
    !sloLoading &&
    !sloError &&
    sloData !== null &&
    (sloData.overall_status === 'HEALTHY' || sloData.active_breaches_count === 0);

  const breachCount = sloData?.active_breaches_count ?? 0;

  return (
    <SLOContext.Provider
      value={{
        sloData,
        sloLoading,
        sloError,
        refetchSLO: fetchSLO,
        isHealthy,
        breachCount,
      }}
    >
      {children}
    </SLOContext.Provider>
  );
};

export const useSLO = (): SLOContextType => {
  const ctx = useContext(SLOContext);
  if (!ctx) throw new Error('useSLO must be used inside <SLOProvider>');
  return ctx;
};
