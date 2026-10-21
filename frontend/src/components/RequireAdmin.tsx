import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';
import { isAdmin, isAuthenticated, signInPath } from '../lib/auth';

const RequireAdmin: React.FC = () => {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to={signInPath(`${location.pathname}${location.search}${location.hash}`)} replace />;
  }

  return isAdmin() ? <Outlet /> : <Navigate to="/dashboard" replace />;
};

export default RequireAdmin;
