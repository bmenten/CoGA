import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import api from '../../lib/api';

const SignupPage: React.FC = () => {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [affiliation, setAffiliation] = useState('');
  const [password, setPassword] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/auth/signup', {
        email,
        password,
        first_name: firstName,
        last_name: lastName,
        affiliation,
      });
      navigate('/login');
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card !max-w-[760px]">
        <div className="auth-gallery">
          <div className="auth-gallery-inner">
            <div>
              <p className="page-kicker !text-[rgba(255,255,255,0.62)]">Account Access</p>
              <h2 className="auth-gallery-title">Create a measured, shared review space.</h2>
              <p className="auth-gallery-copy">
                New accounts can be used to review families, manage projects, and collaborate on
                genomic interpretation inside the same workspace.
              </p>
            </div>
          </div>
        </div>
        <div className="auth-form">
          <p className="page-kicker">Register</p>
          <h1 className="mt-2">Create account</h1>
          <p className="page-subtitle mt-3">
            Use your professional details so administrators can assign the right access.
          </p>
          <form onSubmit={handleSubmit} className="field-grid">
            <label className="field-label">
              First Name
              <input
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                placeholder="First Name"
              />
            </label>
            <label className="field-label">
              Last Name
              <input
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                placeholder="Last Name"
              />
            </label>
            <label className="field-label">
              Email
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email"
              />
            </label>
            <label className="field-label">
              Affiliation
              <input
                value={affiliation}
                onChange={(e) => setAffiliation(e.target.value)}
                placeholder="Affiliation"
              />
            </label>
            <label className="field-label">
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
              />
            </label>
            <button type="submit" className="form-button w-full justify-center">
              Sign Up
            </button>
          </form>
          <p className="mt-6 text-center text-sm text-[var(--color-text-muted)]">
            Already registered?{' '}
            <Link to="/login" className="subtle-link !inline-flex">
              Return to login
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
};

export default SignupPage;
