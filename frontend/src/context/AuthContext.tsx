import React, { createContext, useContext, useEffect, useState } from 'react';
import { ensureSessionToken } from '../api/client';

interface AuthContextType {
  apiKey: string;
  clientId: string;
  role: 'admin' | 'analyst';
  setApiKey: (key: string) => void;
  setRole: (role: 'admin' | 'analyst') => void;
  setClientId: (id: string) => void;
  getHeaders: () => Record<string, string>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [apiKey, setApiKey] = useState<string>(() => {
    // No hardcoded key fallback: an unconfigured key must stay empty, never a baked-in credential.
    return localStorage.getItem('paypilot_api_key') || '';
  });
  const [clientId, setClientId] = useState<string>(() => {
    return localStorage.getItem('paypilot_client_id') || 'merchant_enterprise_01';
  });
  const [role, setRole] = useState<'admin' | 'analyst'>('admin');

  // Automatically acquire a session token from the backend when no manual key is set.
  // The session token is stored in-memory in the API client module (never in localStorage
  // or the JS bundle) and grants analyst-level access via the backend's CORS-protected
  // /api/v1/auth/session endpoint.
  useEffect(() => {
    if (!apiKey) {
      ensureSessionToken().catch(() => {
        // Silent: session token acquisition failures are non-fatal;
        // the API client will retry on the next API call.
      });
    }
  }, [apiKey]);

  const updateApiKey = (key: string) => {
    setApiKey(key);
    localStorage.setItem('paypilot_api_key', key);
  };

  const updateClientId = (id: string) => {
    setClientId(id);
    localStorage.setItem('paypilot_client_id', id);
  };

  const getHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Client-ID': clientId,
    };
    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }
    return headers;
  };

  return (
    <AuthContext.Provider
      value={{
        apiKey,
        clientId,
        role,
        setApiKey: updateApiKey,
        setRole,
        setClientId: updateClientId,
        getHeaders,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
