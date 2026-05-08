import React from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { isAdmin, isAuthenticated } from '../lib/auth';

const RequireAdmin: React.FC = () => {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }

  return isAdmin() ? <Outlet /> : <Navigate to="/dashboard" replace />;
};

export default RequireAdmin;
