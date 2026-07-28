import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';
import { isAuthenticated } from '../lib/auth';

const RequireAuth: React.FC = () => {
  const location = useLocation();
  if (isAuthenticated()) {
    return <Outlet />;
  }

  const next = `${location.pathname}${location.search}${location.hash}`;
  return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
};

export default RequireAuth;
