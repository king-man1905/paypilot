import React, { createContext, useContext, useState } from 'react';

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
    return localStorage.getItem('paypilot_api_key') || 'paypilot-prod-analyst-key';
  });
  const [clientId, setClientId] = useState<string>(() => {
    return localStorage.getItem('paypilot_client_id') || 'merchant_enterprise_01';
  });
  const [role, setRole] = useState<'admin' | 'analyst'>('admin');

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
