import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';
import { isAdmin, isAuthenticated } from '../lib/auth';

const RequireAdmin: React.FC = () => {
  const location = useLocation();
  if (!isAuthenticated()) {
    const next = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return isAdmin() ? <Outlet /> : <Navigate to="/dashboard" replace />;
};

export default RequireAdmin;
