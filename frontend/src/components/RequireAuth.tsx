import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';
import { isAuthenticated, signInPath } from '../lib/auth';

const RequireAuth: React.FC = () => {
  const location = useLocation();
  if (isAuthenticated()) {
    return <Outlet />;
  }

  return <Navigate to={signInPath(`${location.pathname}${location.search}${location.hash}`)} replace />;
};

export default RequireAuth;
